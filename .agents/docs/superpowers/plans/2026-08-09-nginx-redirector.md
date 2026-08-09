# The nginx Redirector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **PROJECT RULE (AGENTS.md):** agents never commit or push — the human driver
> does. Implementers stop at "verified in working tree".

**Goal:** `ghr-pypi index --assets redirect --target nginx` produces a working private index on stock nginx — no njs, no `auth_request`, no module to install.

**Architecture:** `NginxTarget` declares `supports_redirect` and, in redirect mode only, emits a second file holding the allow-list as a generated `map`. The existing server-context snippet gains an `/_assets/` location that authenticates with `auth_basic`, refuses anything absent from the map, and `proxy_pass`es to GitHub's asset API with an operator-supplied token, returning GitHub's 302 to the client.

**Tech Stack:** Python 3.12+, hatchling, typer, pytest; stock nginx (`auth_basic`, `map`, `proxy_pass` — all built by default).

**Spec:** `.agents/docs/superpowers/specs/2026-08-09-nginx-redirector-design.md`

**Context for the implementer:**
- Current: 411 Python tests, 81 worker tests, `just check-all` exit 0.
- **NEVER run `git checkout`** — much of the tree is uncommitted. Revert with `Edit`.
- **Do NOT run `just test-all <path>`** — that recipe forwards its arguments to `uv run` as *flags*, not to pytest as paths, and with `--isolated --exact` it destroys the project venv. Use `just test <path>`.
- **Leave nothing untracked in the repo root** beyond what is gitignored.
- Read `AGENTS.md` at the repo root before starting.

**Orientation — read these before Task 1:**
- `src/ghr_pypi/targets/nginx.py` — the whole current target, ~55 lines.
- `src/ghr_pypi/targets/cloudflare.py` — the same job for the other host, and the model for branching on `site.assets` and for `supports_redirect`.
- `src/ghr_pypi/index.py:334-410` — `_asset_id`, `redirect_urls`, `write_manifest`. The published URL shape and the collision rule both come from here.
- `src/ghr_pypi/targets/__init__.py:29-83` — `SiteContext`.

**Facts you will need:**
- `redirect_urls()` publishes `../../_assets/<asset_id>/<filename>`, relative to `simple/<project>/`. The URI that reaches nginx is therefore `/_assets/<asset_id>/<filename>` when the site is served from the root.
- `FileEntry` carries `api_url` (the asset's GitHub API endpoint) and `metadata_api_url` (the PEP 658 sidecar's, empty when the release has none).
- `index.API_ROOT` is `"https://api.github.com"`. Use it; do not hardcode the host a second time.
- `index._asset_id(api_url)` returns the trailing numeric id and raises `RedirectError` if the URL does not end in one.

---

### Task 1: `supports_redirect` and the two generated artifacts

**Goal:** In redirect mode `NginxTarget` emits an allow-list map plus a server-context location that proxies to GitHub with a token it never writes.

**Files:**
- Modify: `src/ghr_pypi/targets/nginx.py`
- Modify: `tests/test_targets.py:117` and add new tests

**Acceptance Criteria:**
- [ ] `NginxTarget.supports_redirect is True`
- [ ] `ghr-pypi index --assets redirect --target nginx` no longer exits 1
- [ ] In redirect mode `emit` returns **both** `target_dir/ghr-pypi.conf` and `target_dir/ghr-pypi-assets.conf`; in link and mirror mode it returns only the first and the second does not exist
- [ ] Nothing is written to `out_dir` in any mode
- [ ] The map has one entry per file, keyed on `/_assets/<id>/<filename>`, valued with the asset's **API path** (not the full URL, not the bare id)
- [ ] A file whose release has a sidecar gets a second entry keyed `…<filename>.metadata` resolving to the **sidecar's** id, not the wheel's
- [ ] A file whose release has no sidecar gets no `.metadata` key
- [ ] Two entries claiming one asset id raise `RedirectError` at build time
- [ ] The generated config contains **no token and no password**, and `include`s the two operator-written files by absolute path
- [ ] The `auth_basic` realm is a fixed literal, never interpolated from `site.title`
- [ ] `proxy_pass` targets `index.API_ROOT`
- [ ] A **commented** stanza shows how to put the same `auth_basic` in front of the index pages, with a note on when that is and is not wanted
- [ ] `map_hash_bucket_size` is addressed — either set with a measured justification, or deliberately omitted with the reason recorded

**Verify:** `just test tests/test_targets.py` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests** in `tests/test_targets.py`. Reuse the existing `context()` helper. Add this fixture beside it:

```python
def redirect_projects():
    """Two files, one with a PEP 658 sidecar and one without."""
    return {
        "pkg": [
            {
                "filename": "pkg-1.0-py3-none-any.whl",
                "url": "../../_assets/12345/pkg-1.0-py3-none-any.whl",
                "sha256": None,
                "size": 0,
                "upload_time": None,
                "api_url": "https://api.github.com/repos/o/lib/releases/assets/12345",
                "core_metadata": True,
                "metadata_api_url": (
                    "https://api.github.com/repos/o/lib/releases/assets/12346"
                ),
                "source_repo": "o/lib",
                "yanked": False,
            },
            {
                "filename": "pkg-1.0.tar.gz",
                "url": "../../_assets/12347/pkg-1.0.tar.gz",
                "sha256": None,
                "size": 0,
                "upload_time": None,
                "api_url": "https://api.github.com/repos/o/lib/releases/assets/12347",
                "core_metadata": False,
                "metadata_api_url": "",
                "source_repo": "o/lib",
                "yanked": False,
            },
        ]
    }


def parse_nginx_map(text: str) -> dict[str, str]:
    """Parse the generated `map` block into {key: value}.

    A real parse, not a substring check: this project has twice shipped
    assertions on generated config that could never fail. A map collapsed to
    one line, or a block that never opens, must break this.
    """
    body = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    match = re.search(r"map\s+\$uri\s+\$ghr_pypi_asset\s*\{(.*?)\n\}", body, re.S)
    assert match is not None, "no `map $uri $ghr_pypi_asset { ... }` block"
    entries: dict[str, str] = {}
    for raw in match.group(1).splitlines():
        line = raw.strip().rstrip(";")
        if not line or line.startswith("default"):
            continue
        key, value = re.findall(r'"([^"]*)"', line)
        assert key not in entries, f"duplicate map key: {key!r}"
        entries[key] = value
    return entries
```

Then the tests:

```python
def test_nginx_supports_redirect():
    # inverts the old assertion: this target now ships the redirect config.
    assert get_target("nginx").supports_redirect is True


def test_nginx_redirect_writes_both_files(tmp_path):
    site = context(tmp_path, assets="redirect", projects=redirect_projects())
    written = get_target("nginx").emit(site)
    assert set(written) == {
        site.target_dir / "ghr-pypi.conf",
        site.target_dir / "ghr-pypi-assets.conf",
    }
    assert list(site.out_dir.iterdir()) == []


@pytest.mark.parametrize("assets", ["link", "mirror"])
def test_nginx_writes_no_map_outside_redirect_mode(tmp_path, assets):
    site = context(tmp_path / assets, assets=assets, projects=redirect_projects())
    written = get_target("nginx").emit(site)
    assert list(written) == [site.target_dir / "ghr-pypi.conf"]
    assert not (site.target_dir / "ghr-pypi-assets.conf").exists()


def test_nginx_map_lists_every_asset_by_api_path(tmp_path):
    site = context(tmp_path, assets="redirect", projects=redirect_projects())
    get_target("nginx").emit(site)
    entries = parse_nginx_map((site.target_dir / "ghr-pypi-assets.conf").read_text())
    assert entries == {
        "/_assets/12345/pkg-1.0-py3-none-any.whl": (
            "/repos/o/lib/releases/assets/12345"
        ),
        "/_assets/12345/pkg-1.0-py3-none-any.whl.metadata": (
            "/repos/o/lib/releases/assets/12346"
        ),
        "/_assets/12347/pkg-1.0.tar.gz": "/repos/o/lib/releases/assets/12347",
    }


def test_nginx_map_omits_metadata_without_a_sidecar(tmp_path):
    # the sdist has metadata_api_url "": a .metadata key would 302 to the
    # wrong asset or to nothing at all. Absent is the correct answer.
    site = context(tmp_path, assets="redirect", projects=redirect_projects())
    get_target("nginx").emit(site)
    entries = parse_nginx_map((site.target_dir / "ghr-pypi-assets.conf").read_text())
    assert "/_assets/12347/pkg-1.0.tar.gz.metadata" not in entries


def test_nginx_map_refuses_a_colliding_asset_id(tmp_path):
    projects = redirect_projects()
    projects["pkg"][1]["api_url"] = projects["pkg"][0]["api_url"]
    site = context(tmp_path, assets="redirect", projects=projects)
    with pytest.raises(index.RedirectError, match="12345"):
        get_target("nginx").emit(site)


def test_nginx_redirect_config_holds_no_credentials(tmp_path):
    site = context(tmp_path, assets="redirect", projects=redirect_projects())
    get_target("nginx").emit(site)
    for name in ("ghr-pypi.conf", "ghr-pypi-assets.conf"):
        text = (site.target_dir / name).read_text()
        assert "Bearer" not in text
        assert "ghp_" not in text
        assert "proxy_set_header Authorization" not in text
    conf = (site.target_dir / "ghr-pypi.conf").read_text()
    assert "include /etc/nginx/ghr-pypi-token.conf;" in conf
    assert "auth_basic_user_file /etc/nginx/ghr-pypi.htpasswd;" in conf


def test_nginx_realm_is_not_taken_from_the_title(tmp_path):
    # site.title is user-controlled config. Interpolating it into a quoted
    # nginx string is the same injection this project already shipped once
    # via check_slug into wrangler.toml.
    site = context(
        tmp_path,
        assets="redirect",
        projects=redirect_projects(),
        title='evil"; return 200 "pwned',
    )
    get_target("nginx").emit(site)
    conf = (site.target_dir / "ghr-pypi.conf").read_text()
    assert "pwned" not in conf


def test_nginx_redirect_proxies_to_the_api_root(tmp_path):
    site = context(tmp_path, assets="redirect", projects=redirect_projects())
    get_target("nginx").emit(site)
    conf = (site.target_dir / "ghr-pypi.conf").read_text()
    assert f"proxy_pass {index.API_ROOT}$ghr_pypi_asset;" in conf
```

Add `from ghr_pypi import index` to the imports at the top of `tests/test_targets.py`.

- [ ] **Step 2: Run them and watch them fail.**

```
just test tests/test_targets.py
```

Expected: `test_nginx_supports_redirect` fails on the missing attribute, the rest on the missing file or missing config. **Also expect the pre-existing `test_target_protocol_is_satisfied`-adjacent assertion at `tests/test_targets.py:117` to fail** — it asserts `not hasattr(get_target("nginx"), "supports_redirect")`. Delete that line; the spec calls for exactly this inversion, and `test_nginx_supports_redirect` replaces it.

- [ ] **Step 3: Implement** in `src/ghr_pypi/targets/nginx.py`. Add to the imports:

```python
from urllib.parse import urlsplit

from ghr_pypi import index
```

Add the map builder:

```python
def _asset_map(site: SiteContext) -> dict[str, str]:
    """Map every published download URI to the GitHub asset API path behind it.

    The value is the API *path*, not the bare id: an index may aggregate many
    repositories, so the repository has to travel with the entry. It is not the
    full URL either, because the host belongs in one place — the `proxy_pass`
    line — rather than repeated once per asset.

    A collision is refused rather than resolved last-wins, matching
    ``write_manifest``: the dropped entry would keep its published index URL
    and become un-servable, which is a worse failure than a loud one at build
    time.
    """
    entries: dict[str, str] = {}
    claimed: dict[str, str] = {}
    # `index._asset_id` is private and used here across a module boundary.
    # That is a deliberate call, not an oversight: it is the single definition
    # of "the trailing id of an asset URL, shape-checked", and a second copy
    # here would be the thing that drifts. If you would rather promote it to
    # `index.asset_id`, do that and update `write_manifest` too — but do one
    # or the other and say which, rather than duplicating the parse.
    for files in site.projects.values():
        for entry in files:
            asset_id = index._asset_id(entry["api_url"])
            if asset_id in claimed:
                raise index.RedirectError(
                    f"{entry['filename']}: asset id {asset_id} is already "
                    f"claimed by {claimed[asset_id]}; the map cannot list both"
                )
            claimed[asset_id] = entry["filename"]
            key = f"/_assets/{asset_id}/{entry['filename']}"
            entries[key] = urlsplit(entry["api_url"]).path
            if entry["metadata_api_url"]:
                entries[f"{key}.metadata"] = urlsplit(
                    entry["metadata_api_url"]
                ).path
    return entries
```

Add the two templates. **Read every line before you type it; if you find a flaw, fix it and say so in your report rather than transcribing faithfully.** My drafts in this project have averaged several real defects each.

```python
_MAP_HEADER = """\
# Generated by ghr-pypi. Operator artifact — do NOT publish it with the site.
#
# REGENERATED ON EVERY BUILD: this file is overwritten, so any edit made here
# is lost the next time the index is built.
#
# Include this at *http* level, NOT inside a server block — `map` is only
# valid in http context:
#
#     http {{
#         include /etc/nginx/ghr-pypi-assets.conf;
#         server {{
#             include /etc/nginx/ghr-pypi.conf;
#         }}
#     }}
#
# Each key is a download URL this index published; each value is the GitHub
# asset API path it stands for. A request for anything not listed here is
# refused, which is what stops a leaked index URL from becoming a fetch of
# every asset the token happens to be able to read.
map $uri $ghr_pypi_asset {{
    default "";
{entries}}}
"""

_REDIRECT_LOCATION = """
# --- redirect mode ---------------------------------------------------------
# Serves private release assets without mirroring them: nginx holds the token,
# GitHub answers with a signed 302, and the client follows it. The asset bytes
# never pass through this server.
#
# Requires ghr-pypi-assets.conf included at http level, plus two files this
# build deliberately does NOT write, because a build tool must not put
# credentials into its own output:
#
#   /etc/nginx/ghr-pypi-token.conf
#       proxy_set_header Authorization "Bearer ghp_...";
#
#   /etc/nginx/ghr-pypi.htpasswd
#       htpasswd -B -c /etc/nginx/ghr-pypi.htpasswd yourname
#
# Both should be owned by root and readable only by the nginx user. nginx
# refuses to start when either is missing — that is the intended failure. A
# redirector that started without its credentials would authenticate nobody,
# and one that started without its allow-list would be an open proxy onto
# everything the token can read.
location /_assets/ {
    auth_basic "ghr-pypi";
    auth_basic_user_file /etc/nginx/ghr-pypi.htpasswd;

    # Not in the allow-list: refuse. `if` with `return` is one of the two uses
    # of `if` inside a location that nginx documents as safe.
    if ($ghr_pypi_asset = "") {
        return 404;
    }

    # A proxy_pass containing a variable is re-resolved per request, and nginx
    # does not read /etc/resolv.conf — without this every download fails at
    # runtime while the server still starts cleanly. Replace with your own
    # resolver if you have one.
    resolver 1.1.1.1 8.8.8.8 valid=300s;

    include /etc/nginx/ghr-pypi-token.conf;

    proxy_http_version 1.1;
    # api.github.com is virtual-hosted and TLS-SNI-routed: both of these are
    # required or the handshake reaches the wrong certificate.
    proxy_set_header Host api.github.com;
    proxy_ssl_server_name on;
    proxy_set_header Accept application/octet-stream;

    # Hand GitHub's 302 to the client rather than following it — following it
    # would put every package download through this server, which is the one
    # thing redirect mode exists to avoid.
    proxy_redirect off;
    proxy_intercept_errors off;
    proxy_pass {api_root}$ghr_pypi_asset;
}
"""
```

Note the brace convention: `_MAP_HEADER` is `.format()`ted so its literal braces are doubled; `_REDIRECT_LOCATION` has exactly one substitution (`{api_root}`) and its nginx braces are **not** doubled, so it must be concatenated with `.replace("{api_root}", index.API_ROOT)` rather than `.format()`. Pick one convention and make it consistent — the mismatch above is deliberate, to make you decide rather than copy. State which you chose.

Then rewrite `emit`:

```python
    def emit(self, site: SiteContext) -> Sequence[Path]:
        """Write ``ghr-pypi.conf``, plus the allow-list map in redirect mode.

        Deliberately not ``out_dir``: a server configuration written into the
        published tree would be served to anyone who can reach the index.

        The realm is a fixed literal rather than ``site.title``. The title
        comes from user configuration, and interpolating it into a quoted
        nginx string is a config injection — the same shape as the one that
        reached ``wrangler.toml`` through ``check_slug``.
        """
        snippet = _SNIPPET.format(root=site.out_dir.resolve())
        written = []
        if site.assets == "redirect":
            entries = _asset_map(site)
            rendered = "".join(
                f'    "{key}" "{value}";\n' for key, value in sorted(entries.items())
            )
            assets_conf = site.target_dir / "ghr-pypi-assets.conf"
            assets_conf.write_text(
                _MAP_HEADER.format(entries=rendered), encoding="utf-8"
            )
            written.append(assets_conf)
            snippet += _REDIRECT_LOCATION.replace("{api_root}", index.API_ROOT)
        conf = site.target_dir / "ghr-pypi.conf"
        conf.write_text(snippet, encoding="utf-8")
        return (conf, *written)
```

- [ ] **Step 4a: The commented index-page stanza.** Append to `_REDIRECT_LOCATION`:

```
# The index pages themselves are NOT behind auth_basic above — only the
# assets are. Uncomment this to gate the pages too:
#
#   location /simple/ {
#       auth_basic "ghr-pypi";
#       auth_basic_user_file /etc/nginx/ghr-pypi.htpasswd;
#   }
#
# Public pages listing private files is a legitimate configuration: the
# filenames are already disclosed by any index, and the assets stay gated.
# The reverse — gated pages, open assets — is not, and is what the map above
# exists to prevent.
```

Assert in a test that this stanza is present **and commented**: every line of it starts with `#` after stripping leading whitespace. A stanza that shipped uncommented would silently 401 the index pages.

- [ ] **Step 4b: `map_hash_bucket_size`.** Long map keys can exceed nginx's default bucket size, and the failure is a startup error naming the directive. Find the real threshold rather than setting it defensively: the default is processor-cache-line dependent (32/64/128 bytes), and the constraint is on **key length**, not entry count. A key here is `/_assets/<id>/<filename>` — so a long wheel filename is the risk, not a large index.

  Determine whether a realistic worst-case filename exceeds the default. If it does, emit the directive in the map file with a comment stating the measured bound; if it does not, emit nothing and record that finding in your report. **Do not set it "just in case"** — an unnecessary directive in generated config is a claim about a constraint that does not exist. Task 2's live nginx run is where a wrong answer surfaces, so add a long-filename case to `redirect_projects()` if you conclude it matters.

- [ ] **Step 4c: Verify the map keys cannot break out of their quotes.**

nginx map keys are quoted strings; a `"` or newline in a filename would end the string and inject a directive. `index._unsafe_name` already hard-validates filenames — **confirm that yourself** by reading it, and confirm it rejects `"`, newline and `;`. If it does not, raise `RedirectError` in `_asset_map` for any filename outside `[A-Za-z0-9._+-]` and add a test. Report what you found either way; do not assume the existing validation is sufficient just because it exists.

- [ ] **Step 5: Run the tests.**

```
just test tests/test_targets.py
```

Expected: all pass.

- [ ] **Step 6: Confirm the CLI now accepts the combination.** `cli.py:271-284` reads `supports_redirect` via `getattr` and builds its error message from `available_targets()`, so it should need no change — verify rather than assume:

```
uv run ghr-pypi index o/lib --assets redirect --target nginx --out /tmp/nx-site 2>&1 | head -3
```

Expected: it gets past the target check and fails later on the network or the token, **not** with "cannot serve 'assets: redirect'". Also check `tests/test_cli.py` for any test asserting nginx is refused redirect mode, and for one asserting the *message* enumerating capable targets — that list now reads `cloudflare, nginx` and such a test must be updated.

- [ ] **Step 7: Mutations (do not skip).** Reverting each with `Edit`:
  1. Drop the `.metadata` entry → confirm `test_nginx_map_omits_metadata_without_a_sidecar`'s sibling (the full-map equality test) fails.
  2. Use `entry["api_url"]` instead of `urlsplit(...).path` → confirm the map-value test fails.
  3. Interpolate `site.title` into the realm → confirm `test_nginx_realm_is_not_taken_from_the_title` fails.
  4. Remove the collision check → confirm `test_nginx_map_refuses_a_colliding_asset_id` fails.

  Report all four.

- [ ] **Step 8:** `just fix`; `just check-types`; `just test` (report the count).

*(Driver checkpoint: commit as "Add the nginx redirector")*

---

### Task 2: A live nginx run

**Goal:** The generated config is executed by real nginx, not just compared as text.

**Files:**
- Create: `tests/test_nginx_live.py`

**Acceptance Criteria:**
- [ ] The test skips cleanly with a reason when `nginx` is not on `PATH`
- [ ] `nginx -t` accepts the generated pair
- [ ] An allow-listed asset returns the upstream's 302, with its `Location` intact
- [ ] A URI not in the map returns 404
- [ ] A request without credentials returns 401
- [ ] The `Authorization` header the config sends upstream appears in **no** client-visible response
- [ ] A `.metadata` URI resolves to the sidecar's id, not the wheel's
- [ ] The stub upstream records the `Authorization` it received, proving the token reached GitHub's side and not the client's

**Verify:** `just test tests/test_nginx_live.py` → passes, or skips with a reason when nginx is absent

**Steps:**

- [ ] **Step 1: Write the test.** The one seam: the generated config names `https://api.github.com`, which the test cannot reach or stub by DNS. Substitute it for a local address **after** generating, and assert the real value separately in Task 1's unit test (`test_nginx_redirect_proxies_to_the_api_root`) so the substitution cannot hide a wrong host.

```python
"""The generated nginx config, executed by real nginx.

Config asserted only as text is config nothing has run. This project has twice
shipped assertions on generated config that could never fail — a `types{` with
no space, a `_headers` file collapsed to one line — which is why this exists.
"""

import base64
import http.server
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

import pytest

from ghr_pypi.targets import get_target

from tests.test_targets import context, redirect_projects

nginx = pytest.mark.skipif(
    shutil.which("nginx") is None, reason="nginx is not installed"
)

SIGNED = "https://objects.example.com/signed?token=abc"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Upstream(http.server.BaseHTTPRequestHandler):
    """Stands in for api.github.com, recording what it was sent."""

    seen: list[tuple[str, str | None]] = []

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler's spelling
        Upstream.seen.append((self.path, self.headers.get("Authorization")))
        self.send_response(302)
        self.send_header("Location", SIGNED)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def live(tmp_path):
    """Generate the config, point it at a stub upstream, and run nginx."""
    site = context(tmp_path, assets="redirect", projects=redirect_projects())
    get_target("nginx").emit(site)

    upstream_port = free_port()
    server = http.server.HTTPServer(("127.0.0.1", upstream_port), Upstream)
    Upstream.seen = []
    threading.Thread(target=server.serve_forever, daemon=True).start()

    (tmp_path / "htpasswd").write_text(
        # "pw" hashed with nginx-compatible apr1; generated once, not at runtime
        "user:$apr1$Zx8Qk1sQ$mZ4dGm0oGJqk1Yy1lPQ0V/\n",
        encoding="utf-8",
    )
    (tmp_path / "token.conf").write_text(
        'proxy_set_header Authorization "Bearer test-token";\n', encoding="utf-8"
    )

    conf = (site.target_dir / "ghr-pypi.conf").read_text()
    conf = conf.replace("https://api.github.com", f"http://127.0.0.1:{upstream_port}")
    conf = conf.replace(
        "/etc/nginx/ghr-pypi.htpasswd", str(tmp_path / "htpasswd")
    ).replace("/etc/nginx/ghr-pypi-token.conf", str(tmp_path / "token.conf"))

    listen = free_port()
    root = tmp_path / "nginx"
    (root / "logs").mkdir(parents=True)
    (root / "conf").mkdir(parents=True)
    (root / "conf" / "server.conf").write_text(conf, encoding="utf-8")
    (root / "conf" / "assets.conf").write_text(
        (site.target_dir / "ghr-pypi-assets.conf").read_text(), encoding="utf-8"
    )
    (root / "nginx.conf").write_text(
        f"""
daemon off;
events {{}}
error_log {root / "logs" / "error.log"};
pid {root / "nginx.pid"};
http {{
    access_log off;
    include {root / "conf" / "assets.conf"};
    server {{
        listen 127.0.0.1:{listen};
        include {root / "conf" / "server.conf"};
    }}
}}
""",
        encoding="utf-8",
    )

    check = subprocess.run(
        ["nginx", "-t", "-p", str(root), "-c", str(root / "nginx.conf")],
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stderr

    proc = subprocess.Popen(
        ["nginx", "-p", str(root), "-c", str(root / "nginx.conf")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", listen), timeout=0.1):
                break
        except OSError:
            time.sleep(0.05)
    else:  # pragma: no cover - only on a broken environment
        proc.terminate()
        pytest.fail("nginx did not start")

    yield f"http://127.0.0.1:{listen}"

    proc.terminate()
    proc.wait(timeout=10)
    server.shutdown()


def fetch(url, *, auth=("user", "pw")):
    request = urllib.request.Request(url)
    if auth is not None:
        raw = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        request.add_header("Authorization", f"Basic {raw}")
    opener = urllib.request.build_opener(NoRedirect)
    try:
        return opener.open(request)
    except urllib.error.HTTPError as error:
        return error


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


@nginx
def test_an_allow_listed_asset_returns_the_upstream_redirect(live):
    response = fetch(f"{live}/_assets/12345/pkg-1.0-py3-none-any.whl")
    assert response.status == 302
    assert response.headers["Location"] == SIGNED
    path, authorization = Upstream.seen[-1]
    assert path == "/repos/o/lib/releases/assets/12345"
    assert authorization == "Bearer test-token"


@nginx
def test_a_path_outside_the_allow_list_is_refused(live):
    response = fetch(f"{live}/_assets/99999/anything.whl")
    assert response.status == 404
    assert Upstream.seen == []


@nginx
def test_credentials_are_required(live):
    response = fetch(f"{live}/_assets/12345/pkg-1.0-py3-none-any.whl", auth=None)
    assert response.status == 401
    assert Upstream.seen == []


@nginx
def test_the_token_never_reaches_the_client(live):
    for url, auth in (
        (f"{live}/_assets/12345/pkg-1.0-py3-none-any.whl", ("user", "pw")),
        (f"{live}/_assets/99999/nope.whl", ("user", "pw")),
        (f"{live}/_assets/12345/pkg-1.0-py3-none-any.whl", None),
    ):
        response = fetch(url, auth=auth)
        body = response.read()
        assert b"test-token" not in body
        assert "test-token" not in str(response.headers)


@nginx
def test_metadata_resolves_to_the_sidecar(live):
    fetch(f"{live}/_assets/12345/pkg-1.0-py3-none-any.whl.metadata")
    path, _ = Upstream.seen[-1]
    # 12346, the sidecar — not 12345, the wheel.
    assert path == "/repos/o/lib/releases/assets/12346"
```

**The htpasswd hash above is a placeholder you must replace.** Generate a real one and paste the literal:

```
htpasswd -nbm user pw
```

If `htpasswd` is unavailable, `openssl passwd -apr1 pw` produces the same format. Do **not** compute it at test runtime — a hash generated by the same code that checks it proves nothing. Report the command you used.

- [ ] **Step 2: Run it.**

```
just test tests/test_nginx_live.py
```

Expected: 5 passed, or 5 skipped with "nginx is not installed". If nginx is absent locally, install it (`brew install nginx`) and report that you did — a test suite that only ever skips is the failure mode this task exists to prevent.

- [ ] **Step 3: Mutations (do not skip).** Reverting each with `Edit` to `src/ghr_pypi/targets/nginx.py`:
  1. Change `return 404;` to `return 200;` → confirm the allow-list test fails.
  2. Remove the `auth_basic` lines → confirm the credentials test fails.
  3. Point the `.metadata` map entry at the wheel's id → confirm the sidecar test fails.
  4. Remove `proxy_redirect off;` → report whether anything fails. If nothing does, say so plainly rather than inventing a test: it may genuinely be belt-and-braces given the upstream Location does not match the proxy_pass prefix.

  Report all four.

- [ ] **Step 4:** `just test` (report the count); `just check-all > /tmp/gate.log 2>&1; echo EXIT=$?` → `EXIT=0`.

*(Driver checkpoint: commit as "Run real nginx against the generated redirector config")*

---

### Task 3: The docs and the gate

**Goal:** The nginx redirector is documented everywhere the Cloudflare one is, and its live tests actually run in CI.

**Files:**
- Modify: `doc/source/reference/targets.rst`, `doc/source/how-to/private-packages-without-mirroring.rst`, `doc/source/changelog.rst`, `direction.md`, `.github/workflows/test.yml`, `pyproject.toml`

**Acceptance Criteria:**
- [ ] The Linux CI leg installs nginx, so `tests/test_nginx_live.py` **runs** rather than skips
- [ ] `addopts` gains `-ra`, so a skip is visible in CI output rather than a bare count
- [ ] `targets.rst` lists `nginx` as redirect-capable and describes both emitted files
- [ ] It states that `map` is `http`-context-only, which is *why* there are two files
- [ ] The how-to gains an nginx section covering the two operator-written files, the fixed realm, and the resolver
- [ ] It states plainly that `ghr-pypi` never writes the token or the htpasswd, and why
- [ ] `changelog.rst` has a bullet
- [ ] `direction.md`'s remaining target work narrows to `_redirects` alone
- [ ] `just check-all` → exit 0 and a warning-free strict HTML build

**Verify:** `just check-all > /tmp/gate.log 2>&1; echo EXIT=$?` → `EXIT=0`

**Steps:**

- [ ] **Step 1: `doc/source/reference/targets.rst`.** Find the `supports_redirect` section (around line 50) and the per-target descriptions. Add `nginx` to the redirect-capable set, and document the two files by name — `ghr-pypi.conf` (server context) and `ghr-pypi-assets.conf` (http context) — with the `map`-context reason for the split. Match the page's existing voice; do not restate the whole design.

- [ ] **Step 2: `doc/source/how-to/private-packages-without-mirroring.rst`.** This page is currently Cloudflare-shaped. Add an nginx section covering, in order: generate with `--assets redirect --target nginx`; include the two files at the right contexts; create `ghr-pypi-token.conf` and the htpasswd; the resolver line and when to change it.

  State plainly that the build writes neither credential file, and why — a build tool that put a token in its own output would put it wherever that output goes. Note that nginx refuses to start when either is missing, and that this is the intended failure.

- [ ] **Step 3: `changelog.rst`.** A bullet under the current unreleased version: the `nginx` target now supports `assets: redirect`, serving private release assets on stock nginx with no additional modules.

- [ ] **Step 4: `direction.md`.** Under the target-specific artifact generation item, the "what remains" list currently reads:

  > - the same redirector for nginx, as an njs or `auth_request` location;
  > - `_redirects` alongside `_headers`.

  The first is now done — and done differently from how that line predicted, with no module at all. Remove it and say so in the prose above, so the record shows the njs route was considered and rejected rather than forgotten.

- [ ] **Step 4a: Make the live nginx tests run in CI.**

`tests/test_nginx_live.py` skips wherever `nginx` is absent, and **no runner image ships it** — so all 10 tests skip on every CI run today. `just check-all` does not mask this because it never runs pytest at all; what masks it is that `addopts` has no `-ra`, so the output reads "N passed, 10 skipped" with no reason line.

A suite that only ever skips is exactly the failure that suite was written to prevent.

In `.github/workflows/test.yml`, add an nginx install step to the **Linux** job only, before the test step:

```yaml
      - name: Install nginx
        run: sudo apt-get update && sudo apt-get install -y nginx
```

Do not add it to macOS or Windows: the point is that the suite executes somewhere every push, not everywhere. Match the file's existing step style, and put it after the checkout/setup steps.

In `pyproject.toml`, add `-ra` to pytest's `addopts` so any future skip states its reason in CI output.

**Verify it actually runs rather than trusting the YAML.** Confirm `nginx` is what the job would get by checking the step lands in the job that runs pytest — `just check-all` does not, so putting it in the wrong job would look right and change nothing. Report which job you modified and why.

- [ ] **Step 5: Gate.**

```
just fix
just test
just test-worker
just check-all > /tmp/gate.log 2>&1; echo EXIT=$?
uv run --no-default-groups --group docs sphinx-build -b html -a -E -n ./doc/source /tmp/nx-docs
```

`EXIT=0` and a warning-free strict build. Then:

```bash
grep -rn "supports_redirect\|njs\|auth_request" --include='*.rst' --include='*.md' doc/source README.md direction.md
git status --short
```

Judge every hit and report both. In particular, any surviving claim that nginx *cannot* serve redirect mode, or that njs is required, is now false.

*(Driver checkpoint: commit as "Document the nginx redirector")*

---

## After the plan

Driver: commit the three checkpoints. The tutorial sub-project follows, and its
nginx tutorial is written against this — which is why it was sequenced second.
