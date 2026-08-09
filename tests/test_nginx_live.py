"""The generated nginx config, executed by real nginx.

Config asserted only as text is config nothing has run. This project has
shipped four assertions on generated config that could never fail — a `types{`
with no space, a `_headers` file collapsed to one line, and an `auth_basic`
check that a commented-out example stanza elsewhere in the same file satisfied,
so deleting the real directive from the location guarding private assets passed
the whole suite. Every test here starts a real nginx, so a config that parses
but does not do the job fails.

Two seams this file cannot close, both covered in ``tests/test_targets.py``
instead:

- The generated ``proxy_pass`` names ``index.API_ROOT``, which no test can
  reach or stub by DNS, so the fixture rewrites it to a local stub *after*
  generation. ``test_nginx_derives_the_proxy_host_from_the_api_root`` asserts
  the real value by moving the constant. The ``Host`` header is *not*
  rewritten, so the value the upstream sees here is the generated one.
- The ``include`` paths under ``/etc/nginx/``, and the CA bundle
  ``proxy_ssl_trusted_certificate`` names, are rewritten the same way — all
  three are absolute paths belonging to the operator's machine. They are
  asserted as text in ``test_nginx_gates_the_assets_location_with_auth_basic``
  and ``test_nginx_redirect_proxies_to_the_api_root``.

The ``resolver`` address is a third seam that this file cannot narrow at all:
rewriting ``proxy_pass`` to a literal IP means nothing is ever resolved, so
``resolver 6.6.6.6;`` would pass every test here. It is pinned as text.
"""

import base64
import dataclasses
import http.client
import http.server
import shutil
import socket
import ssl
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from ghr_pypi import index
from ghr_pypi.targets import get_target
from tests.test_targets import context, redirect_projects

# Module-level rather than a decorator per test: a decorator can be left off a
# test added later, and that test would then error on a machine without nginx
# instead of skipping.
pytestmark = pytest.mark.skipif(
    shutil.which("nginx") is None, reason="nginx is not installed"
)

# What the stub upstream answers with, standing in for GitHub's signed S3 URL.
SIGNED = "https://objects.example.com/signed?token=abc"

# The bearer token the operator's ghr-pypi-token.conf would hold. Every test
# that can see a response checks this string is not in it.
TOKEN = "test-token"  # a fake, and the point is that it leaks nowhere

# Generated once with `openssl passwd -apr1 -salt Zx8Qk1sQ pw`, and pasted as a
# literal: a hash computed at test runtime by the same code that checks it
# would prove nothing about whether nginx can verify it. htpasswd's -m produces
# the same apr1 format. nginx implements apr1 itself, so this verifies
# identically on macOS and Linux, where a bcrypt hash would not.
HTPASSWD = "user:$apr1$Zx8Qk1sQ$FBoV6acxl/sxxh5Ph62no0\n"
PASSWORD = ("user", "pw")

# From redirect_projects(): a wheel with a PEP 658 sidecar asset in the release.
WHEEL = "/_assets/12345/pkg-1.0-py3-none-any.whl"

# The wheel added below, whose release carries no sidecar, so `missing_metadata:
# extract` (the default) wrote one into the site as an ordinary static file.
EXTRACTED = "/_assets/12348/other-2.0-py3-none-any.whl.metadata"
SIDECAR = b"Metadata-Version: 2.1\nName: other\nVersion: 2.0\n"


def projects_with_an_extracted_sidecar():
    """``redirect_projects()`` plus a wheel whose sidecar was extracted.

    ``extract_missing_metadata`` writes ``_assets/<id>/<filename>.metadata``
    into the site for exactly this case — a wheel with no ``metadata_api_url``
    — and the index advertises that URL. It is therefore a real file under
    ``/_assets/`` that is deliberately *absent* from the allow-list map, which
    is what makes it the case a single proxying location would 404.
    """
    projects = redirect_projects()
    wheel = dict(projects["pkg"][0])
    wheel.update(
        filename="other-2.0-py3-none-any.whl",
        url="../../_assets/12348/other-2.0-py3-none-any.whl",
        api_url="https://api.github.com/repos/o/lib/releases/assets/12348",
        metadata_api_url="",
        core_metadata=True,
    )
    projects["pkg"].append(wheel)
    return projects


# The CA bundle path the target generates. Rewritten by the fixture, because
# nginx opens it at configuration load and it is a Debian path.
CA_PATH = "/etc/ssl/certs/ca-certificates.crt"


def ca_bundle() -> str | None:
    """A CA bundle this machine actually has, for the rewritten config.

    Any parsable bundle will do — nothing here speaks TLS. What is being
    checked is that the generated ``proxy_ssl_*`` directives load at all.
    """
    for candidate in (
        CA_PATH,
        ssl.get_default_verify_paths().cafile,
        "/etc/ssl/cert.pem",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return None  # pragma: no cover — a machine with no CA store at all


def free_port() -> int:
    """A port nothing holds. Only for nginx, which has to bind it itself.

    Inherently racy — the socket is closed before nginx binds — so anything
    that can be handed a bound socket instead should be.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclasses.dataclass
class Request:
    """One request the stub upstream received."""

    path: str
    authorization: str | None
    host: str | None
    user_agent: str | None


@dataclasses.dataclass
class Live:
    """A running nginx and the requests its stub upstream received."""

    port: int
    seen: list[Request]


def upstream_handler(seen: list[Request]):
    """A stand-in for api.github.com that records what it was sent."""

    class Upstream(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler's spelling
            seen.append(
                Request(
                    path=self.path,
                    authorization=self.headers.get("Authorization"),
                    host=self.headers.get("Host"),
                    user_agent=self.headers.get("User-Agent"),
                )
            )
            self.send_response(302)
            self.send_header("Location", SIGNED)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    return Upstream


@dataclasses.dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def __str__(self) -> str:
        return f"{self.status} {self.headers} {self.body!r}"


def fetch(live: Live, path: str, *, credentials=PASSWORD) -> Response:
    """GET ``path`` from the running nginx, without following the redirect.

    ``http.client`` rather than ``urllib``: a 302 is the expected answer on the
    happy path, so an opener that follows it — or that returns ``None`` when
    told not to — would obscure the one header this whole design exists to
    deliver.
    """
    connection = http.client.HTTPConnection("127.0.0.1", live.port, timeout=10)
    headers = {}
    if credentials is not None:
        raw = base64.b64encode(":".join(credentials).encode()).decode()
        headers["Authorization"] = f"Basic {raw}"
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        return Response(
            status=response.status,
            headers=dict(response.getheaders()),
            body=response.read(),
        )
    finally:
        connection.close()


@pytest.fixture
def live(tmp_path):
    """Generate the config, point it at a stub upstream, and run nginx."""
    site = context(
        tmp_path, assets="redirect", projects=projects_with_an_extracted_sidecar()
    )
    get_target("nginx").emit(site)

    # what `missing_metadata: extract` leaves in the site tree
    sidecar = site.out_dir / EXTRACTED.lstrip("/")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_bytes(SIDECAR)

    bundle = ca_bundle()
    if bundle is None:  # pragma: no cover — a machine with no CA store at all
        pytest.skip("no CA bundle to point proxy_ssl_trusted_certificate at")

    seen: list[Request] = []
    # Port 0, not free_port(): the kernel hands over a port already bound, so
    # there is no window in which something else can take it.
    upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), upstream_handler(seen))
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    try:
        yield from _serve(tmp_path, site, upstream, seen, bundle)
    finally:
        # Everything between here and the `yield` can fail — a config nginx
        # rejects, a port that went away — and an upstream left bound would
        # outlive the test and its thread would outlive the session.
        upstream.shutdown()
        upstream.server_close()


def _serve(tmp_path, site, upstream, seen, bundle):
    """Rewrite the generated config for a local stub and run nginx on it."""
    (tmp_path / "htpasswd").write_text(HTPASSWD, encoding="utf-8")
    (tmp_path / "token.conf").write_text(
        f'proxy_set_header Authorization "Bearer {TOKEN}";\n', encoding="utf-8"
    )

    conf = (site.target_dir / "ghr-pypi.conf").read_text(encoding="utf-8")
    # The one substitution the test cannot avoid, and the reason
    # test_nginx_derives_the_proxy_host_from_the_api_root exists. Only
    # proxy_pass changes: the `Host` header keeps the generated value, so what
    # the stub records below is the real one.
    upstream_url = "http://127.0.0.1:{}".format(upstream.server_address[1])
    assert index.API_ROOT in conf
    conf = conf.replace(index.API_ROOT, upstream_url)
    # `proxy_ssl_server_name on;` is deliberately left in place even though the
    # stub is plaintext. It was measured: nginx takes the scheme from the
    # literal prefix of proxy_pass, so with an http:// upstream the directive
    # is inert rather than an error, and every test here passes with it. One
    # fewer difference between the config that ships and the config that runs.
    conf = conf.replace("/etc/nginx/ghr-pypi.htpasswd", str(tmp_path / "htpasswd"))
    conf = conf.replace("/etc/nginx/ghr-pypi-token.conf", str(tmp_path / "token.conf"))
    # `proxy_ssl_verify on;` and its depth are left exactly as generated. Only
    # the bundle path is rewritten, and it has to be: nginx reads the file at
    # configuration load even for a plaintext upstream, so the generated
    # Debian path makes `nginx -t` fail anywhere that file is absent. That is
    # the intended failure — the operator finds out on deploy — and it means
    # this fixture proves the emitted directives load, while
    # test_nginx_redirect_proxies_to_the_api_root asserts the shipped path.
    assert CA_PATH in conf
    conf = conf.replace(CA_PATH, bundle)

    listen = free_port()
    root = tmp_path / "nginx"
    (root / "logs").mkdir(parents=True)
    (root / "conf").mkdir(parents=True)
    (root / "conf" / "server.conf").write_text(conf, encoding="utf-8")
    (root / "conf" / "assets.conf").write_text(
        (site.target_dir / "ghr-pypi-assets.conf").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    # The `location ~ \.(whl|metadata)$` below is the hostile neighbour, and it
    # is in the server block for *every* test in this file rather than one of
    # them: an operator adding a cache-header rule for wheels is ordinary, and
    # nginx prefers a matching regex location over a plain prefix one. Without
    # `^~` on the assets location, that empty block answers every asset request
    # with no auth_basic in scope — 404 for the proxied files, and the file
    # itself for the extracted sidecars sitting in the site. The temp paths are
    # redirected into tmp_path because the compiled-in defaults are absolute
    # and nginx creates them at startup, which a non-root CI user cannot do.
    (root / "nginx.conf").write_text(
        f"""\
daemon off;
events {{}}
error_log {root / "logs" / "error.log"};
pid {root / "nginx.pid"};
http {{
    access_log off;
    client_body_temp_path {root / "client_body_temp"};
    proxy_temp_path {root / "proxy_temp"};
    fastcgi_temp_path {root / "fastcgi_temp"};
    uwsgi_temp_path {root / "uwsgi_temp"};
    scgi_temp_path {root / "scgi_temp"};
    include {root / "conf" / "assets.conf"};
    server {{
        listen 127.0.0.1:{listen};
        include {root / "conf" / "server.conf"};
        location ~ \\.(whl|metadata)$ {{ }}
    }}
}}
""",
        encoding="utf-8",
    )

    binary = shutil.which("nginx") or "nginx"
    check = subprocess.run(
        [binary, "-t", "-p", str(root), "-c", "nginx.conf"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stderr

    process = subprocess.Popen(
        [binary, "-p", str(root), "-c", "nginx.conf"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(200):
            try:
                with socket.create_connection(("127.0.0.1", listen), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.05)
        else:  # pragma: no cover — only on a broken environment
            pytest.fail(
                "nginx did not start: "
                + (root / "logs" / "error.log").read_text(encoding="utf-8")
            )
        yield Live(port=listen, seen=seen)
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_an_allow_listed_asset_returns_the_upstream_redirect(live):
    # The whole point of redirect mode: GitHub's 302 reaches the client
    # unaltered, so the asset bytes never pass through this server.
    response = fetch(live, WHEEL)
    assert response.status == 302, response
    assert response.headers["Location"] == SIGNED
    assert [request.path for request in live.seen] == [
        "/repos/o/lib/releases/assets/12345"
    ]


def test_the_token_goes_upstream_and_the_api_host_travels_with_it(live):
    # The Authorization header the client sent was Basic; what leaves for the
    # upstream is the operator's bearer token, and the Host header is the
    # generated one — this fixture rewrites only proxy_pass.
    assert fetch(live, WHEEL).status == 302
    (request,) = live.seen
    assert request.authorization == f"Bearer {TOKEN}"
    assert request.host == urlsplit(index.API_ROOT).netloc
    # GitHub answers 403 to a request with no User-Agent
    assert request.user_agent == "ghr-pypi"


def test_a_metadata_uri_resolves_to_the_sidecars_own_asset(live):
    # 12346, the sidecar asset — not 12345, the wheel it describes. Serving the
    # wheel here would hand a multi-megabyte binary to a resolver expecting a
    # few kilobytes of metadata.
    response = fetch(live, f"{WHEEL}.metadata")
    assert response.status == 302, response
    assert [request.path for request in live.seen] == [
        "/repos/o/lib/releases/assets/12346"
    ]


def test_an_extracted_sidecar_is_served_from_disk(live):
    # `missing_metadata: extract` (the default) writes this file into the site,
    # and the index advertises it, but it is not in the allow-list — there is
    # no GitHub asset behind it. A location that proxied everything under
    # /_assets/ would 404 exactly the metadata the index promises.
    response = fetch(live, EXTRACTED)
    assert response.status == 200, response
    assert response.body == SIDECAR
    assert live.seen == []


def test_the_extracted_sidecar_is_behind_auth_basic_too(live):
    # try_files serves that file before the allow-list is consulted, so it is
    # the one path where the authentication could plausibly have been skipped.
    # It is real package metadata from a private release.
    response = fetch(live, EXTRACTED, credentials=None)
    assert response.status == 401, response
    assert SIDECAR not in response.body
    assert live.seen == []


def test_a_uri_outside_the_allow_list_is_refused(live):
    # A leaked index URL must not become a fetch of every asset the token can
    # read: the upstream is not contacted at all.
    response = fetch(live, "/_assets/99999/anything.whl")
    assert response.status == 404, response
    assert live.seen == []


def test_credentials_are_required(live):
    response = fetch(live, WHEEL, credentials=None)
    assert response.status == 401, response
    # the realm is the fixed literal, not the user-controlled site title
    assert response.headers["WWW-Authenticate"] == 'Basic realm="ghr-pypi"'
    assert live.seen == []


def test_wrong_credentials_are_refused(live):
    response = fetch(live, WHEEL, credentials=("user", "not-pw"))
    assert response.status == 401, response
    assert live.seen == []


def test_a_competing_regex_location_cannot_take_the_assets(live):
    # The regression test for `^~`. The fixture's server block contains
    # `location ~ \.(whl|metadata)$ { }` — the cache-header rule an operator
    # writes — which nginx prefers over a plain prefix location. Drop the `^~`
    # and this file's static sidecar is served to anyone who asks, while the
    # proxied wheels 404: private files out, downloads broken.
    #
    # Every test in this file runs against that hostile neighbour, so most of
    # these assertions are made elsewhere too. The duplication is deliberate:
    # the property has one named home that states it in full, and it survives
    # any later decision to make the neighbour a per-test fixture.
    leak = fetch(live, EXTRACTED, credentials=None)
    assert leak.status == 401, leak
    assert SIDECAR not in leak.body

    unauthenticated = fetch(live, WHEEL, credentials=None)
    assert unauthenticated.status == 401, unauthenticated

    # ...and the prefix location still routes, rather than merely winning
    assert fetch(live, WHEEL).status == 302


def test_the_token_never_reaches_the_client(live):
    for path, credentials in (
        (WHEEL, PASSWORD),
        (f"{WHEEL}.metadata", PASSWORD),
        ("/_assets/99999/nope.whl", PASSWORD),
        (EXTRACTED, PASSWORD),
        (WHEEL, None),
        (EXTRACTED, None),
        (WHEEL, ("user", "not-pw")),
    ):
        response = fetch(live, path, credentials=credentials)
        assert TOKEN.encode() not in response.body, response
        assert TOKEN not in str(response.headers), response
    # and it did reach the upstream on the paths that proxy, so the assertions
    # above are about a token that exists
    assert {request.authorization for request in live.seen} == {f"Bearer {TOKEN}"}
