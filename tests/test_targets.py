"""Tests for the pluggable deployment target registry."""

import dataclasses
import re
from collections.abc import Sequence
from importlib import resources
from urllib.parse import urlsplit

import pytest

from ghr_pypi import index, targets
from ghr_pypi.targets import SiteContext, Target, available_targets, get_target
from ghr_pypi.targets import cloudflare as _cloudflare  # noqa: F401 — for monkeypatch

PACKAGED_WORKER = (
    resources.files("ghr_pypi.targets").joinpath("_worker.js").read_bytes()
)


def context(tmp_path, **overrides):
    defaults = dict(
        projects={},
        out_dir=tmp_path / "site",
        target_dir=tmp_path / "ops",
        title="Package index",
        index_url=None,
        assets="link",
        formats=("html", "json"),
    )
    defaults.update(overrides)
    site = SiteContext(**defaults)
    site.out_dir.mkdir(parents=True, exist_ok=True)
    site.target_dir.mkdir(parents=True, exist_ok=True)
    return site


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


# the snippet is included *inside* a server block, so its top level is server
# context rather than any location
SERVER = "<server>"

# `^~` and all: the modifier is part of what makes this location correct, so
# the tests name the location by the header that has it.
ASSETS = "^~ /_assets/"


def uncommented(text: str) -> str:
    """The lines nginx acts on — every comment stripped."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def parse_nginx_map(text: str) -> dict[str, str]:
    """Parse the generated `map` block into {key: value}.

    A real parse, not a substring check: this project has twice shipped
    assertions on generated config that could never fail. A map collapsed to
    one line, or a block that never opens, must break this.
    """
    body = uncommented(text)
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


def parse_nginx_locations(text: str) -> dict[str, list[str]]:
    """Split the server snippet into ``{location header: [directive, ...]}``.

    Comments go first, so no assertion can be satisfied by the commented
    ``/simple/`` example — that is exactly how deleting both ``auth_basic``
    lines from the assets location once left all 431 tests passing. Directives
    are grouped by the block they are in, so "the assets location is
    authenticated" cannot be satisfied by an ``auth_basic`` somewhere else in
    the file, and "``proxy_pass`` is configured" cannot be satisfied by one in
    the wrong block. Nested blocks (``if``) are flattened into their location,
    since the question is always which location a directive belongs to.
    """
    blocks: dict[str, list[str]] = {SERVER: []}
    header = SERVER
    depth = 0
    for raw in uncommented(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "}":
            depth -= 1
            assert depth >= 0, "unbalanced braces in the snippet"
            if depth == 0:
                header = SERVER
            continue
        if line.endswith("{"):
            opener = line[:-1].strip()
            if depth == 0:
                assert opener.startswith("location "), f"unexpected block: {opener!r}"
                header = opener[len("location ") :].strip()
                assert header not in blocks, f"duplicate location: {header!r}"
                blocks[header] = []
            else:
                blocks[header].append(opener)
            depth += 1
            continue
        blocks[header].append(line.rstrip(";"))
    assert depth == 0, "unbalanced braces in the snippet"
    return blocks


def parse_cloudflare_headers(text: str) -> dict[str, dict[str, str]]:
    """Parse a Cloudflare Pages ``_headers`` file into ``{path: {header: value}}``.

    A real parse — not a bare substring check — is what catches the file
    degenerating into one unparseable line: every substring a naive
    assertion looks for can still be present in that mutation.
    """
    rules: dict[str, dict[str, str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        if raw[:1].isspace():
            assert current is not None, f"header line before any path rule: {raw!r}"
            assert raw.startswith("  ") and not raw.startswith("   "), (
                f"header line must be indented exactly two spaces: {raw!r}"
            )
            key, _, value = raw.strip().partition(":")
            rules[current][key.strip()] = value.strip()
        else:
            current = raw.strip()
            assert current not in rules, f"duplicate rule path: {current!r}"
            rules[current] = {}
    return rules


def test_site_context_is_frozen(tmp_path):
    site = context(tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        site.title = "nope"  # type: ignore[misc]


def test_builtin_targets_are_registered():
    assert set(available_targets()) >= {"static", "cloudflare", "nginx"}


def test_get_target_returns_a_target():
    target = get_target("static")
    assert isinstance(target, Target)
    assert target.name == "static"


def test_get_target_lists_the_alternatives():
    with pytest.raises(
        ValueError, match="unknown target 'nope'; available: "
    ) as caught:
        get_target("nope")
    _, _, listed = str(caught.value).partition("available: ")
    assert {"cloudflare", "nginx", "static"} <= set(listed.split(", "))


def test_cloudflare_writes_headers(tmp_path):
    site = context(tmp_path)
    assert list(get_target("cloudflare").emit(site)) == [site.out_dir / "_headers"]
    rules = parse_cloudflare_headers((site.out_dir / "_headers").read_text())
    assert rules["/simple/*"] == {
        "Cache-Control": "public, max-age=300, must-revalidate"
    }
    assert list(site.target_dir.iterdir()) == []


def test_cloudflare_omits_file_rules_without_mirroring(tmp_path):
    site = context(tmp_path, assets="link")
    get_target("cloudflare").emit(site)
    rules = parse_cloudflare_headers((site.out_dir / "_headers").read_text())
    assert not any(path.startswith("/files/") for path in rules)


def test_cloudflare_caches_mirrored_files_immutably(tmp_path):
    site = context(tmp_path, assets="mirror")
    get_target("cloudflare").emit(site)
    rules = parse_cloudflare_headers((site.out_dir / "_headers").read_text())
    assert rules["/files/*"] == {"Cache-Control": "public, max-age=31536000, immutable"}
    assert rules["/files/*.metadata"] == {"Content-Type": "application/octet-stream"}


def test_supports_redirect_is_optional():
    # read with getattr(..., False) by the CLI, so a target predating the
    # attribute keeps working — which is exactly what `static` is.
    assert get_target("cloudflare").supports_redirect is True
    assert not hasattr(get_target("static"), "supports_redirect")


def test_cloudflare_ships_the_worker_verbatim(tmp_path):
    # byte-identical, not merely present: the Worker is security-critical and
    # is node-tested as a file, so a target that reformatted or templated it
    # would be deploying code nothing tests.
    site = context(tmp_path, assets="redirect")
    get_target("cloudflare").emit(site)
    assert (site.out_dir / "_worker.js").read_bytes() == PACKAGED_WORKER


def test_cloudflare_redirect_writes_operator_artifacts_to_the_target_dir(tmp_path):
    site = context(tmp_path, assets="redirect")
    written = get_target("cloudflare").emit(site)
    assert set(written) == {
        site.out_dir / "_worker.js",
        site.target_dir / "wrangler.toml",
        site.target_dir / "SETUP.md",
    }
    # _worker.js is consumed by Pages at deploy time and never served; the
    # other two are operator artifacts and must never reach the published tree
    assert not (site.out_dir / "wrangler.toml").exists()
    assert not (site.out_dir / "SETUP.md").exists()


def test_cloudflare_writes_no_headers_file_in_redirect_mode(tmp_path):
    # Cloudflare: custom headers in _headers are not applied to responses
    # generated by Pages Functions, and in advanced mode every response is one.
    # The file would be inert, and it would advertise a `public` cache policy
    # for a site this mode puts behind Basic auth. The Worker sets the real
    # policy on what it delegates; tests/worker/ covers that.
    site = context(tmp_path, assets="redirect")
    get_target("cloudflare").emit(site)
    assert not (site.out_dir / "_headers").exists()


def test_cloudflare_setup_uses_the_pages_wrangler_commands(tmp_path):
    # `wrangler secret put` targets a Worker. A Pages project needs
    # `wrangler pages secret put`, or the secrets are bound to nothing and the
    # deploy 401s every request — the exact symptom this file diagnoses.
    site = context(tmp_path, assets="redirect")
    get_target("cloudflare").emit(site)
    setup = (site.target_dir / "SETUP.md").read_text()
    assert re.search(r"^wrangler secret put ", setup, re.M) is None
    assert "wrangler pages secret put" in setup


def test_cloudflare_setup_points_at_the_guide(tmp_path):
    # SETUP.md is deliberately a checklist: the reasoning lives in the how-to,
    # so the same facts are not maintained in two places. That only holds while
    # the file actually says where to go.
    site = context(tmp_path, assets="redirect")
    get_target("cloudflare").emit(site)
    setup = (site.target_dir / "SETUP.md").read_text()
    assert "private-packages-without-mirroring" in setup


def test_cloudflare_setup_binds_secrets_before_deploying(tmp_path):
    # Cloudflare: secrets must be set "before a deployment that uses those
    # secrets", and a binding added later needs a redeploy to take effect. But
    # `pages secret put` needs the project to exist, so creating it has to come
    # first — otherwise the only working order is deploy, bind, redeploy.
    site = context(tmp_path, assets="redirect")
    get_target("cloudflare").emit(site)
    setup = (site.target_dir / "SETUP.md").read_text()
    create = setup.index("wrangler pages project create")
    secret = setup.index("wrangler pages secret put")
    deploy = setup.index("wrangler pages deploy")
    assert create < secret < deploy
    assert "redeploy" in setup


def test_cloudflare_setup_explains_the_bad_token_symptom(tmp_path):
    # 401 diagnoses the two credential secrets; without this, the third one has
    # no symptom on the page at all.
    site = context(tmp_path, assets="redirect")
    get_target("cloudflare").emit(site)
    setup = (site.target_dir / "SETUP.md").read_text()
    assert "502" in setup
    assert "GITHUB_TOKEN" in setup


def test_cloudflare_says_its_operator_artifacts_are_regenerated(tmp_path):
    # both are overwritten every build, and wrangler.toml invites a rename
    site = context(tmp_path, assets="redirect")
    get_target("cloudflare").emit(site)
    assert (
        "REGENERATED ON EVERY BUILD" in (site.target_dir / "wrangler.toml").read_text()
    )
    assert "regenerated on every build" in (site.target_dir / "SETUP.md").read_text()


def test_cloudflare_falls_back_to_an_absolute_output_dir(tmp_path, monkeypatch):
    # Windows raises ValueError for two paths on different drives, which is the
    # one case where no relative path exists to write.
    def no_relative_path(path, start):
        raise ValueError("path is on mount 'C:', start on mount 'D:'")

    monkeypatch.setattr(targets.cloudflare.os.path, "relpath", no_relative_path)
    site = context(tmp_path, assets="redirect")
    get_target("cloudflare").emit(site)
    toml = (site.target_dir / "wrangler.toml").read_text()
    assert f'pages_build_output_dir = "{site.out_dir.resolve().as_posix()}"' in toml


def test_cloudflare_setup_documents_all_three_secrets(tmp_path):
    site = context(tmp_path, assets="redirect")
    get_target("cloudflare").emit(site)
    setup = (site.target_dir / "SETUP.md").read_text()
    for secret in ("GHR_PYPI_USER", "GHR_PYPI_PASSWORD", "GITHUB_TOKEN"):
        assert f"wrangler pages secret put {secret}" in setup
    # all three are required: the Worker refuses every request with 401 when
    # either credential secret is unbound, which reads as a broken deploy
    # rather than as the missing step it is
    assert "401" in setup
    assert "wrangler pages deploy" in setup


def test_cloudflare_wrangler_points_at_the_site(tmp_path):
    site = context(tmp_path, assets="redirect")
    get_target("cloudflare").emit(site)
    toml = (site.target_dir / "wrangler.toml").read_text()
    assert "pages_build_output_dir" in toml
    assert "../site" in toml


@pytest.mark.parametrize("assets", ["link", "mirror"])
def test_cloudflare_emits_no_redirect_artifacts_otherwise(tmp_path, assets):
    site = context(tmp_path / assets, assets=assets)
    written = get_target("cloudflare").emit(site)
    assert list(written) == [site.out_dir / "_headers"]
    for name in ("_worker.js", "wrangler.toml", "SETUP.md"):
        assert not (site.out_dir / name).exists()
        assert not (site.target_dir / name).exists()


def test_nginx_writes_the_snippet_to_the_target_dir(tmp_path):
    site = context(tmp_path)
    assert list(get_target("nginx").emit(site)) == [site.target_dir / "ghr-pypi.conf"]
    conf = (site.target_dir / "ghr-pypi.conf").read_text()
    assert f'root "{site.out_dir.resolve()}";' in conf
    assert "index index.html;" in conf
    assert "try_files $uri $uri/ =404;" in conf
    assert "default_type application/octet-stream;" in conf
    assert "absolute_redirect off;" in conf


def test_nginx_publishes_nothing(tmp_path):
    # The whole point of target_dir: a server snippet under out_dir would be
    # served to anyone who can reach the index.
    site = context(tmp_path)
    get_target("nginx").emit(site)
    assert list(site.out_dir.iterdir()) == []


def test_nginx_does_not_override_the_mime_map(tmp_path):
    # A `types { }` block in a server context REPLACES the inherited map,
    # which would break the content type of every .html and .json we emit.
    # Strip comments first and match the directive itself (allowing no space
    # before the brace) rather than the literal six characters "types {",
    # which the explanatory comment in the snippet also happens to contain.
    site = context(tmp_path)
    get_target("nginx").emit(site)
    conf = (site.target_dir / "ghr-pypi.conf").read_text()
    body = "\n".join(
        line for line in conf.splitlines() if not line.lstrip().startswith("#")
    )
    assert re.search(r"^\s*types\s*\{", body, re.M) is None


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
    blocks = parse_nginx_locations((site.target_dir / "ghr-pypi.conf").read_text())
    assert set(blocks) == {SERVER, "/"}


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


@pytest.mark.parametrize(
    "filename",
    [
        'pkg-1.0";return 200 "pwned-py3-none-any.whl',
        "pkg-1.0\n    return 200 pwned;\n-py3-none-any.whl",
        "pkg-1.0;return 200 pwned-py3-none-any.whl",
        "pkg 1.0-py3-none-any.whl",
    ],
)
def test_nginx_map_refuses_a_filename_that_could_escape_its_quotes(tmp_path, filename):
    # index._unsafe_name rejects only / \ : and a leading dot, so a `"`, a
    # newline or a `;` reaches a target intact. A map key is a quoted nginx
    # string in an http-context file: closing that quote injects a directive.
    projects = redirect_projects()
    projects["pkg"][0]["filename"] = filename
    site = context(tmp_path, assets="redirect", projects=projects)
    with pytest.raises(index.RedirectError, match="nginx map key"):
        get_target("nginx").emit(site)
    assert not (site.target_dir / "ghr-pypi-assets.conf").exists()


@pytest.mark.parametrize("field", ["api_url", "metadata_api_url"])
def test_nginx_map_refuses_an_api_url_that_could_escape_its_quotes(tmp_path, field):
    # the value side of the same line the filename check guards. asset_id()
    # shape-checks only the trailing segment — "12345" here — so everything
    # before it arrives as free text, and a map value is a quoted string in an
    # http-context file just as a key is. Real nginx accepts the whole injected
    # server block from a config generated without this check.
    projects = redirect_projects()
    projects["pkg"][0][field] = (
        'https://api.github.com/repos/o/lib";}'
        'server{listen 19222;location /pwned{return 200 "pwned";}}'
        'map $uri $j{default "/12345'
    )
    site = context(tmp_path, assets="redirect", projects=projects)
    with pytest.raises(index.RedirectError, match="nginx map value"):
        get_target("nginx").emit(site)
    assert not (site.target_dir / "ghr-pypi-assets.conf").exists()


def test_nginx_map_refuses_an_api_url_that_walks_out_of_its_endpoint(tmp_path):
    # the charset allows dots, so `..` is refused separately: this path is
    # spelled with characters nginx is perfectly happy to quote, and would aim
    # the token at an endpoint that is not this asset.
    projects = redirect_projects()
    projects["pkg"][0]["api_url"] = (
        "https://api.github.com/repos/o/lib/../../../user/keys/12345"
    )
    site = context(tmp_path, assets="redirect", projects=projects)
    with pytest.raises(index.RedirectError, match="nginx map value"):
        get_target("nginx").emit(site)


def test_nginx_map_accepts_the_punctuation_real_wheels_use(tmp_path):
    # the flip side of the test above: underscores, dots, plus signs and
    # hyphens are all ordinary in wheel names and must not be refused.
    projects = redirect_projects()
    projects["pkg"][0]["filename"] = "ghr_pypi-1.0+cu118-cp312-cp312-manylinux_2_17.whl"
    site = context(tmp_path, assets="redirect", projects=projects)
    get_target("nginx").emit(site)
    entries = parse_nginx_map((site.target_dir / "ghr-pypi-assets.conf").read_text())
    assert "/_assets/12345/ghr_pypi-1.0+cu118-cp312-cp312-manylinux_2_17.whl" in entries


def test_nginx_redirect_config_holds_no_credentials(tmp_path):
    # Asserted against the *uncommented* config: the comments deliberately
    # show the operator the shape of the file they must write, placeholder
    # token and all. What must hold is that nginx acts on no credential here.
    site = context(tmp_path, assets="redirect", projects=redirect_projects())
    get_target("nginx").emit(site)
    for name in ("ghr-pypi.conf", "ghr-pypi-assets.conf"):
        text = uncommented((site.target_dir / name).read_text())
        assert "Bearer" not in text
        assert "ghp_" not in text
        assert "proxy_set_header Authorization" not in text
    assets = parse_nginx_locations((site.target_dir / "ghr-pypi.conf").read_text())[
        ASSETS
    ]
    assert "include /etc/nginx/ghr-pypi-token.conf" not in assets
    proxy = parse_nginx_locations((site.target_dir / "ghr-pypi.conf").read_text())[
        "@ghr_pypi_asset"
    ]
    assert "include /etc/nginx/ghr-pypi-token.conf" in proxy


def test_nginx_gates_the_assets_location_with_auth_basic(tmp_path):
    # anchored inside the block that gates private assets, not searched for in
    # the file: both of these lines also appear in the commented /simple/
    # stanza, so a bare substring assertion survives deleting them from here.
    site = context(tmp_path, assets="redirect", projects=redirect_projects())
    get_target("nginx").emit(site)
    assets = parse_nginx_locations((site.target_dir / "ghr-pypi.conf").read_text())[
        ASSETS
    ]
    assert 'auth_basic "ghr-pypi"' in assets
    assert "auth_basic_user_file /etc/nginx/ghr-pypi.htpasswd" in assets


def test_nginx_assets_location_wins_against_a_regex_location(tmp_path):
    # `^~`, not decoration. nginx prefers a regex location over a plain prefix
    # one, so a single `location ~ \.whl$` anywhere in the operator's server
    # block would serve every asset from a block with no auth_basic in it.
    site = context(tmp_path, assets="redirect", projects=redirect_projects())
    get_target("nginx").emit(site)
    blocks = parse_nginx_locations((site.target_dir / "ghr-pypi.conf").read_text())
    assert ASSETS in blocks, f"assets location is not `{ASSETS}`: {sorted(blocks)}"


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
    assert 'auth_basic "ghr-pypi"' in parse_nginx_locations(conf)[ASSETS]


def test_nginx_redirect_proxies_to_the_api_root(tmp_path):
    site = context(tmp_path, assets="redirect", projects=redirect_projects())
    get_target("nginx").emit(site)
    proxy = parse_nginx_locations((site.target_dir / "ghr-pypi.conf").read_text())[
        "@ghr_pypi_asset"
    ]
    assert f"proxy_pass {index.API_ROOT}$ghr_pypi_asset" in proxy
    assert f"proxy_set_header Host {urlsplit(index.API_ROOT).netloc}" in proxy
    assert "proxy_ssl_server_name on" in proxy
    # GitHub answers 403 to a request with no User-Agent, so it is not left to
    # whichever client happened to ask
    assert "proxy_set_header User-Agent ghr-pypi" in proxy
    # proxy_pass with a variable is resolved per request, and nginx does not
    # read /etc/resolv.conf: without this every download fails at runtime.
    # The address is asserted in full, not merely that a resolver line exists:
    # this resolver decides where a request carrying the operator's token
    # actually goes, and `resolver 6.6.6.6;` satisfies every weaker assertion
    # while pointing the token at whoever owns it. The live suite cannot see
    # this — it rewrites proxy_pass to a literal IP, so nothing is resolved.
    assert "resolver 1.1.1.1 8.8.8.8 valid=300s" in proxy
    # ...and the handshake that carries the token to that address is verified.
    # nginx does not do this by default.
    assert "proxy_ssl_verify on" in proxy
    assert "proxy_ssl_verify_depth 2" in proxy
    assert "proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt" in proxy
    # following the 302 here would put every download through this server
    assert "proxy_redirect off" in proxy


def test_nginx_derives_the_proxy_host_from_the_api_root(tmp_path, monkeypatch):
    # The Host header must come from index.API_ROOT rather than being spelled
    # a second time. Asserting `urlsplit(index.API_ROOT).netloc` does not show
    # that: with the real constant it is the string "api.github.com", so a
    # hardcoded literal in the source satisfies it. Moving the constant is the
    # only thing that tells the two apart.
    monkeypatch.setattr(index, "API_ROOT", "https://ghe.example.com/api/v3")
    site = context(tmp_path, assets="redirect", projects=redirect_projects())
    get_target("nginx").emit(site)
    proxy = parse_nginx_locations((site.target_dir / "ghr-pypi.conf").read_text())[
        "@ghr_pypi_asset"
    ]
    assert "proxy_pass https://ghe.example.com/api/v3$ghr_pypi_asset" in proxy
    assert "proxy_set_header Host ghe.example.com" in proxy
    assert "api.github.com" not in "\n".join(proxy)


def test_nginx_serves_an_extracted_sidecar_from_disk(tmp_path):
    # with missing_metadata "extract" (the default) the build writes
    # _assets/<id>/<file>.metadata into the site itself, and the index
    # advertises it. A location that went straight to the allow-list would
    # 404 exactly those files.
    #
    # try_files must be in the *authenticated* block: it is what routes a
    # request onward, so a copy of it in a location without auth_basic would
    # serve those static sidecars — real package metadata — to anyone.
    site = context(tmp_path, assets="redirect", projects=redirect_projects())
    get_target("nginx").emit(site)
    blocks = parse_nginx_locations((site.target_dir / "ghr-pypi.conf").read_text())
    assert "try_files $uri @ghr_pypi_asset" in blocks[ASSETS]
    assert 'auth_basic "ghr-pypi"' in blocks[ASSETS]
    assert "@ghr_pypi_asset" in blocks


def test_nginx_redirect_adds_no_location_beyond_those_two(tmp_path):
    # the whole shape in one assertion: an extra location is a route into the
    # site that nothing in this suite describes, and the commented /simple/
    # stanza showing up here would mean it shipped uncommented.
    site = context(tmp_path, assets="redirect", projects=redirect_projects())
    get_target("nginx").emit(site)
    blocks = parse_nginx_locations((site.target_dir / "ghr-pypi.conf").read_text())
    assert set(blocks) == {SERVER, "/", ASSETS, "@ghr_pypi_asset"}


def test_nginx_refuses_a_uri_outside_the_allow_list(tmp_path):
    site = context(tmp_path, assets="redirect", projects=redirect_projects())
    get_target("nginx").emit(site)
    proxy = parse_nginx_locations((site.target_dir / "ghr-pypi.conf").read_text())[
        "@ghr_pypi_asset"
    ]
    assert 'if ($ghr_pypi_asset = "")' in proxy
    assert "return 404" in proxy


def test_nginx_ships_the_index_page_auth_stanza_commented(tmp_path):
    # uncommented it would 401 the index pages themselves, silently
    site = context(tmp_path, assets="redirect", projects=redirect_projects())
    get_target("nginx").emit(site)
    conf = (site.target_dir / "ghr-pypi.conf").read_text()
    assert "#   location /simple/ {" in conf, "the suggested stanza is missing"
    assert re.search(r"^\s*location /simple/", uncommented(conf), re.M) is None


def test_nginx_raises_the_map_bucket_size_only_when_a_key_needs_it(tmp_path):
    # nginx exits with "could not build map_hash" when one key does not fit a
    # bucket; the bound is key length, not entry count. 64-byte buckets hold
    # 46-byte keys, so a long name needs the directive and a short one does
    # not — and an unnecessary directive claims a constraint that is not real.
    long_name = "pkg-1.0-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
    projects = redirect_projects()
    projects["pkg"][0]["filename"] = long_name
    site = context(tmp_path / "long", assets="redirect", projects=projects)
    get_target("nginx").emit(site)
    long_conf = (site.target_dir / "ghr-pypi-assets.conf").read_text()
    longest = max(len(key) for key in parse_nginx_map(long_conf))
    match = re.search(r"^map_hash_bucket_size (\d+);", uncommented(long_conf), re.M)
    assert match is not None, f"a {longest}-byte key needs the directive"
    assert int(match.group(1)) >= 16 + -(-(longest + 2) // 8) * 8

    short = redirect_projects()
    short["pkg"] = [dict(short["pkg"][1], filename="p-1.tar.gz")]
    site = context(tmp_path / "short", assets="redirect", projects=short)
    get_target("nginx").emit(site)
    short_conf = (site.target_dir / "ghr-pypi-assets.conf").read_text()
    assert max(len(key) for key in parse_nginx_map(short_conf)) <= 46
    assert "map_hash_bucket_size" not in uncommented(short_conf)


def test_nginx_warns_about_map_hash_max_size_only_for_a_large_index(tmp_path):
    # a comment, never a directive: past 2048 entries nginx logs "could not
    # build optimal map_hash" and carries on, and the value that would silence
    # it cannot be computed without reimplementing nginx's hash placement.
    small = context(tmp_path / "small", assets="redirect", projects=redirect_projects())
    get_target("nginx").emit(small)
    assert (
        "map_hash_max_size"
        not in (small.target_dir / "ghr-pypi-assets.conf").read_text()
    )

    files = [
        dict(
            redirect_projects()["pkg"][1],
            filename=f"pkg{n}-1.0.tar.gz",
            api_url=f"https://api.github.com/repos/o/lib/releases/assets/{n}",
        )
        for n in range(2049)
    ]
    big = context(tmp_path / "big", assets="redirect", projects={"pkg": files})
    get_target("nginx").emit(big)
    conf = (big.target_dir / "ghr-pypi-assets.conf").read_text()
    assert len(parse_nginx_map(conf)) == 2049
    assert "# This map has 2049 entries" in conf
    # guidance only — an unrequested directive here would be a claim about a
    # constraint that nginx treats as a warning
    assert "map_hash_max_size" not in uncommented(conf)


def test_nginx_redirect_map_is_empty_when_nothing_is_published(tmp_path):
    # a `map` with only its default is valid nginx and refuses everything,
    # which is the right answer for an index that published no assets.
    site = context(tmp_path, assets="redirect", projects={})
    get_target("nginx").emit(site)
    conf = (site.target_dir / "ghr-pypi-assets.conf").read_text()
    assert parse_nginx_map(conf) == {}
    assert "map_hash_bucket_size" not in uncommented(conf)


def test_static_emits_nothing(tmp_path):
    site = context(tmp_path)
    assert list(get_target("static").emit(site)) == []
    assert list(site.out_dir.iterdir()) == []
    assert list(site.target_dir.iterdir()) == []


def test_every_builtin_reports_exactly_what_it_wrote(tmp_path):
    # the CLI echoes these paths verbatim, so a target that under-reports
    # silently hides an artifact and one that over-reports names a file the
    # operator will not find. Compared as sets and walked recursively: the
    # contract fixes *which* files were written, not their order, and a target
    # is free to write into a subdirectory (target_dir/conf.d/site.conf).
    for name in ("static", "cloudflare", "nginx"):
        site = context(tmp_path / name)
        written = get_target(name).emit(site)
        assert set(written) == {
            path
            for directory in (site.out_dir, site.target_dir)
            for path in directory.rglob("*")
            if path.is_file()
        }
        assert len(set(written)) == len(written), f"{name} reported a path twice"


def test_every_builtin_returns_a_materialized_sequence(tmp_path):
    # not an Iterable: a generator would defer its writes to whenever the CLI
    # consumed the result, escaping the error handling around the emit call
    for name in ("static", "cloudflare", "nginx"):
        site = context(tmp_path / name)
        written = get_target(name).emit(site)
        assert isinstance(written, Sequence) and not isinstance(written, (str, bytes))
        # already on disk before anyone iterates the result
        assert all(path.is_file() for path in written)


class _Plugin:
    # also the worked example a plugin author is most likely to copy, so it
    # returns what it wrote rather than None: @runtime_checkable verifies that
    # `emit` exists, not what it returns, so a None-returning target registers
    # fine and only fails once the CLI reports its artifacts
    name = "demo"

    def emit(self, site):
        written = site.out_dir / "plugin.txt"
        written.write_text("hi")
        return (written,)


class _FakeEntryPoint:
    def __init__(self, name, value="pkg:Thing", factory=_Plugin):
        self.name = name
        self.value = value
        self._factory = factory

    def load(self):
        return self._factory


class _BrokenEntryPoint(_FakeEntryPoint):
    """An entry point pointing at something that is not there."""

    def load(self):
        raise AttributeError("module 'pkg' has no attribute 'Thing'")


class _Instance:
    """What an entry point pointing at an instance rather than a class loads."""

    name = "instance"

    def emit(self, site):
        """Write nothing, and report nothing written."""
        return ()


def test_entry_point_targets_are_merged(monkeypatch):
    monkeypatch.setattr(
        targets, "entry_points", lambda group: [_FakeEntryPoint("demo")]
    )
    assert available_targets()["demo"].name == "demo"


def test_builtins_win_a_name_collision(monkeypatch, capsys):
    monkeypatch.setattr(
        targets, "entry_points", lambda group: [_FakeEntryPoint("static")]
    )
    assert available_targets()["static"].name == "static"
    captured = capsys.readouterr()
    assert "static" in captured.err
    assert "built in" in captured.err
    assert captured.out == ""


def test_a_broken_plugin_is_skipped_with_a_warning(monkeypatch, capsys):
    monkeypatch.setattr(
        targets, "entry_points", lambda group: [_BrokenEntryPoint("broken")]
    )
    assert "broken" not in available_targets()
    captured = capsys.readouterr()
    assert "broken" in captured.err
    assert "pkg:Thing" in captured.err
    assert "has no attribute" in captured.err
    assert captured.out == ""


def test_a_broken_plugin_leaves_the_builtins_usable(monkeypatch):
    monkeypatch.setattr(
        targets, "entry_points", lambda group: [_BrokenEntryPoint("broken")]
    )
    assert get_target("static").name == "static"


def test_a_plugin_resolving_to_an_instance_is_skipped(monkeypatch, capsys):
    monkeypatch.setattr(
        targets,
        "entry_points",
        lambda group: [_FakeEntryPoint("instance", factory=_Instance())],
    )
    assert "instance" not in available_targets()
    assert capsys.readouterr().err.count("instance") >= 1
