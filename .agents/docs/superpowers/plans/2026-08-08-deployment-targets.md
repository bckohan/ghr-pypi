# Deployment Targets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **PROJECT RULE (AGENTS.md):** agents never commit or push — the human driver
> does. Implementers stop at "verified in working tree".

**Goal:** `target: cloudflare` or `target: nginx` writes that host's deployment artifacts alongside the index, through a documented, pluggable `Target` interface.

**Architecture:** A new `ghr_pypi.targets` package holds a `Target` Protocol, a frozen `SiteContext`, and a registry merging built-ins with `ghr_pypi.targets` entry points. `mirror: bool` becomes `assets: link | mirror` so a target can tell whether files are local. Targets only *emit files* — they never rewrite URLs, which is what keeps the later redirect mode from turning the protocol into a grab bag.

**Spec:** `.agents/docs/superpowers/specs/2026-08-08-deployment-targets-design.md` (this is sub-project 1 of 4)

**Context for the implementer:**
- Current suite: 292 tests green, `just check-all` exit 0. The tree carries a
  large amount of uncommitted work — touch only your task's files.
- **NEVER run `git checkout` on a source file.** The whole feature is
  uncommitted; `git checkout` discards it, not just your last edit. This has
  already bitten one implementer. Revert with the `Edit` tool.
- **Do NOT run `just test-all <path>`.** It destroys the project venv. Use
  `just test` or `uv run pytest <path>`.
- `config.py` must never import `index.py` or `targets` — `index` imports
  `config`, and `targets` imports both, so either would close a cycle.
- Python floor is 3.10, so `importlib.metadata.entry_points(group=...)` is
  available.
- doc8 enforces a 100-character line limit on RST.
- Read the files you are changing before writing; the code wins over this plan.

---

### Task 1: `assets` replaces `mirror`

**Goal:** One `assets` key with `link`/`mirror`, `mirror` kept as a deprecated alias.

**Files:**
- Modify: `src/ghr_pypi/config.py`, `src/ghr_pypi/cli.py`
- Modify: `tests/test_config.py`, `tests/test_cli.py`

**Acceptance Criteria:**
- [ ] `AssetMode = Literal["link", "mirror"]` in `config.py` beside `MissingDigest`/`Formats`
- [ ] `Config.assets: AssetMode = "link"` replaces `Config.mirror: bool`
- [ ] `assets: mirror` loads; an invalid value raises listing the valid ones
- [ ] `mirror: true` loads with a deprecation warning on **stderr** and yields `assets == "mirror"`; `mirror: false` yields `"link"`
- [ ] Both keys present raises `ConfigError`
- [ ] `missing_digest` alongside mirroring still raises, keyed on `assets`
- [ ] `--mirror` still works and still conflicts with `--config`
- [ ] Nothing in `index.py` changes

**Verify:** `just test` → all pass (report the count); `just check-types` clean

**Steps:**

- [ ] **Step 1: Write the failing tests.** Add to `tests/test_config.py`, reusing its existing config-writing helper:

```python
def test_assets_defaults_to_link(tmp_path):
    assert load(write(tmp_path, "repositories: [a/b]\n")).assets == "link"


def test_assets_mirror_loads(tmp_path):
    assert load(write(tmp_path, "repositories: [a/b]\nassets: mirror\n")).assets == "mirror"


def test_assets_rejects_an_unknown_mode(tmp_path):
    with pytest.raises(ConfigError, match="'assets' must be one of link, mirror"):
        load(write(tmp_path, "repositories: [a/b]\nassets: sideways\n"))


@pytest.mark.parametrize("value,expected", [("true", "mirror"), ("false", "link")])
def test_mirror_is_a_deprecated_alias(tmp_path, capsys, value, expected):
    cfg = load(write(tmp_path, f"repositories: [a/b]\nmirror: {value}\n"))
    assert cfg.assets == expected
    captured = capsys.readouterr()
    assert "deprecated" in captured.err
    assert "deprecated" not in captured.out


def test_mirror_and_assets_together_are_rejected(tmp_path):
    with pytest.raises(ConfigError, match="not both"):
        load(write(tmp_path, "repositories: [a/b]\nmirror: true\nassets: mirror\n"))


def test_missing_digest_still_rejected_under_mirroring(tmp_path):
    with pytest.raises(ConfigError, match="'missing_digest' has no effect"):
        load(write(tmp_path, "repositories: [a/b]\nassets: mirror\nmissing_digest: omit\n"))
```

In `tests/test_cli.py`, find every assertion referencing `cfg.mirror` or
`Config(mirror=...)` and update it to `assets`. Add:

```python
def test_resolve_mirror_flag_sets_the_asset_mode():
    assert resolve(["a/b"], mirror=True).assets == "mirror"
    assert resolve(["a/b"]).assets == "link"
```

- [ ] **Step 2: Run and confirm they fail.**

Run: `uv run pytest tests/test_config.py -q`
Expected: failures on the missing `assets` attribute.

- [ ] **Step 3: `config.py`.** Add the alias beside the existing ones:

```python
AssetMode = Literal["link", "mirror"]
```

Add `"assets"` to `_KNOWN_KEYS` (keep `"mirror"` — it must still parse). Replace `Config.mirror: bool = False` with `assets: AssetMode = "link"` in the same position. Replace the `mirror` block in `load` (currently ~lines 347-352) with:

```python
    raw_assets = raw.get("assets")
    raw_mirror = raw.get("mirror")
    if raw_assets is not None and raw_mirror is not None:
        raise ConfigError(
            f"{path}: set either 'assets' or the deprecated 'mirror', not both"
        )
    if raw_mirror is not None:
        if not isinstance(raw_mirror, bool):
            raise ConfigError(f"{path}: 'mirror' must be true or false")
        assets: AssetMode = "mirror" if raw_mirror else "link"
        print(
            f"warning: {path}: 'mirror' is deprecated; write assets: {assets}",
            file=sys.stderr,
        )
    else:
        assets = raw_assets if raw_assets is not None else "link"
        if assets not in ("link", "mirror"):
            raise ConfigError(
                f"{path}: 'assets' must be one of link, mirror, got {assets!r}"
            )
    if assets == "mirror" and "missing_digest" in raw:
        raise ConfigError(
            f"{path}: 'missing_digest' has no effect when 'assets' is mirror"
        )
```

Confirm `sys` is imported in `config.py`; add `import sys` if not. Change the `Config(...)` construction from `mirror=mirror` to `assets=assets`.

- [ ] **Step 4: `cli.py`.** Four sites:
  - `_resolve_config`'s `--config` conflict message becomes
    `"with --config, set 'assets: mirror' in the config file"`.
  - Its `Config(...)` construction takes `assets="mirror" if mirror else "link"`.
  - `defer_hash=cfg.mirror` becomes `defer_hash=cfg.assets == "mirror"`.
  - `if cfg.mirror:` becomes `if cfg.assets == "mirror":`.

  Leave the `--mirror` option itself alone apart from its help text, which should mention it is shorthand for `assets: mirror`.

- [ ] **Step 5: Verify.**

Run: `just test` → report the count
Run: `just fix`, then `just check-types` → clean

- [ ] **Step 6: Mutation check.** Change the deprecation `print` to write to stdout instead of stderr → confirm `test_mirror_is_a_deprecated_alias` FAILS. Revert with `Edit`, and report.

*(Driver checkpoint: commit as "Replace the mirror key with assets")*

---

### Task 2: The `targets` package — protocol, context, registry

**Goal:** A documented `Target` Protocol, a frozen `SiteContext`, and a registry merging built-ins with entry points.

**Files:**
- Create: `src/ghr_pypi/targets/__init__.py`, `src/ghr_pypi/targets/static.py`
- Create: `tests/test_targets.py`

**Acceptance Criteria:**
- [ ] `SiteContext` is a frozen dataclass with exactly the documented fields
- [ ] `Target` is a Protocol with `name` and `emit(site)`
- [ ] `available_targets()` returns the built-ins keyed by name
- [ ] Entry points in group `ghr_pypi.targets` are merged in
- [ ] A plugin whose name collides with a built-in is **ignored with a warning on stderr**
- [ ] `get_target("nope")` raises `ValueError` naming every registered target
- [ ] `static` emits nothing
- [ ] No import cycle: `targets` imports `config` and `index`, neither imports `targets`

**Verify:** `uv run pytest tests/test_targets.py -q` → all pass; `just check-types` clean

**Steps:**

- [ ] **Step 1: Write the failing tests** in a new `tests/test_targets.py`:

```python
from pathlib import Path

import pytest

from ghr_pypi import targets
from ghr_pypi.targets import SiteContext, Target, available_targets, get_target


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


def test_site_context_is_frozen(tmp_path):
    site = context(tmp_path)
    with pytest.raises(Exception):
        site.title = "nope"  # type: ignore[misc]


def test_builtin_targets_are_registered():
    assert set(available_targets()) >= {"static", "cloudflare", "nginx"}


def test_get_target_returns_a_target():
    target = get_target("static")
    assert isinstance(target, Target)
    assert target.name == "static"


def test_get_target_lists_the_alternatives():
    with pytest.raises(ValueError, match="unknown target 'nope'; available: "):
        get_target("nope")


def test_static_emits_nothing(tmp_path):
    site = context(tmp_path)
    get_target("static").emit(site)
    assert list(site.out_dir.iterdir()) == []
    assert list(site.target_dir.iterdir()) == []


class _Plugin:
    name = "demo"

    def emit(self, site):
        (site.out_dir / "plugin.txt").write_text("hi")


class _FakeEntryPoint:
    def __init__(self, name, value="pkg:Thing", factory=_Plugin):
        self.name = name
        self.value = value
        self._factory = factory

    def load(self):
        return self._factory


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
```

- [ ] **Step 2: Run and confirm they fail.**

Run: `uv run pytest tests/test_targets.py -q`
Expected: `ModuleNotFoundError: No module named 'ghr_pypi.targets'`.

- [ ] **Step 3: Create `src/ghr_pypi/targets/__init__.py`:**

```python
"""Pluggable emitters for host-specific deployment artifacts.

A target writes the files a particular host needs *alongside* the index —
cache headers, a server snippet — and nothing else. What URLs the index
contains is decided by the ``assets`` mode, not by the target, so one index
can be deployed anywhere and a new target never has to understand link
rewriting.

Import direction is one-way: this package imports ``config`` and ``index``,
and neither imports it. Keep it that way.
"""

import sys
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import Protocol, runtime_checkable

from ghr_pypi.config import AssetMode, Formats
from ghr_pypi.index import Projects

ENTRY_POINT_GROUP = "ghr_pypi.targets"


@dataclass(frozen=True)
class SiteContext:
    """Everything a target may read about a build.

    ``out_dir`` is published; ``target_dir`` is not. An artifact that must
    never be served — a server snippet, a deploy script — belongs in
    ``target_dir``: writing it under ``out_dir`` would publish it to anyone
    who can reach the index.
    """

    projects: Projects
    out_dir: Path
    target_dir: Path
    title: str
    index_url: str | None
    assets: AssetMode
    formats: tuple[Formats, ...]


@runtime_checkable
class Target(Protocol):
    """Writes one host's deployment artifacts."""

    name: str

    def emit(self, site: SiteContext) -> None:
        """Write this target's artifacts for ``site``."""


def _builtins() -> dict[str, Target]:
    # Imported here rather than at module scope: the submodules import
    # SiteContext and Target from this module, so a top-level import would
    # be circular.
    from ghr_pypi.targets.cloudflare import CloudflareTarget
    from ghr_pypi.targets.nginx import NginxTarget
    from ghr_pypi.targets.static import StaticTarget

    return {
        target.name: target
        for target in (StaticTarget(), CloudflareTarget(), NginxTarget())
    }


def available_targets() -> dict[str, Target]:
    """Return every registered target by name.

    Built-ins are merged with any ``ghr_pypi.targets`` entry point, whose
    value must be a callable returning a :class:`Target`. A built-in wins a
    name collision, because an installed dependency must never silently
    change what a build emits.
    """
    registry = _builtins()
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        if entry.name in registry:
            print(
                f"warning: ignoring plugin target {entry.name!r} from "
                f"{entry.value}: that name is built in",
                file=sys.stderr,
            )
            continue
        registry[entry.name] = entry.load()()
    return registry


def get_target(name: str) -> Target:
    """Return the registered target called ``name``.

    Raises ``ValueError`` listing every registered target when there is no
    such name; the CLI turns that into a ``ConfigError``.
    """
    registry = available_targets()
    if name not in registry:
        raise ValueError(
            f"unknown target {name!r}; available: {', '.join(sorted(registry))}"
        )
    return registry[name]
```

- [ ] **Step 4: Create `src/ghr_pypi/targets/static.py`:**

```python
"""The default target: a plain static host needs no artifacts."""

from ghr_pypi.targets import SiteContext


class StaticTarget:
    """Emits nothing.

    It exists so that "no deployment artifacts" is a named choice rather than
    an absence, and so the CLI always has a target to call.
    """

    name = "static"

    def emit(self, site: SiteContext) -> None:
        """Write nothing at all."""
```

Task 3 creates `cloudflare.py` and `nginx.py`; until then `_builtins()` cannot
import them. **Create both files as stubs in this task** so the registry works
— each a class with the right `name` and an `emit` that does nothing — and
Task 3 fills in the bodies. Say in your report that you did this.

- [ ] **Step 5: Verify.**

Run: `uv run pytest tests/test_targets.py -q` → all pass
Run: `just test` → report the count
Run: `just fix`, then `just check-types` → clean

- [ ] **Step 6: Mutation check.** Delete the collision `continue` so plugins overwrite built-ins → confirm `test_builtins_win_a_name_collision` FAILS. Revert with `Edit` and report.

*(Driver checkpoint: commit as "Add the target protocol and registry")*

---

### Task 3: The `cloudflare` and `nginx` targets

**Goal:** Both targets emit correct artifacts, into the correct directory.

**Files:**
- Modify: `src/ghr_pypi/targets/cloudflare.py`, `src/ghr_pypi/targets/nginx.py`
- Modify: `tests/test_targets.py`

**Acceptance Criteria:**
- [ ] `cloudflare` writes `out_dir/_headers` with a `/simple/*` cache rule
- [ ] Under `assets: mirror` it also writes the `/files/*` immutable rule and the `/files/*.metadata` content type; under `assets: link` it writes neither
- [ ] `nginx` writes `target_dir/ghr-pypi.conf` and **nothing into `out_dir`**
- [ ] The nginx snippet sets `default_type`, not a `types` block
- [ ] Both write nothing outside their one file

**Verify:** `uv run pytest tests/test_targets.py -q` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests,** reusing `context()` from Task 2:

```python
def test_cloudflare_writes_headers(tmp_path):
    site = context(tmp_path)
    get_target("cloudflare").emit(site)
    headers = (site.out_dir / "_headers").read_text()
    assert "/simple/*" in headers
    assert "max-age=300" in headers
    assert list(site.target_dir.iterdir()) == []


def test_cloudflare_omits_file_rules_without_mirroring(tmp_path):
    site = context(tmp_path, assets="link")
    get_target("cloudflare").emit(site)
    headers = (site.out_dir / "_headers").read_text()
    assert "/files/" not in headers


def test_cloudflare_caches_mirrored_files_immutably(tmp_path):
    site = context(tmp_path, assets="mirror")
    get_target("cloudflare").emit(site)
    headers = (site.out_dir / "_headers").read_text()
    assert "/files/*" in headers
    assert "immutable" in headers
    assert "/files/*.metadata" in headers
    assert "application/octet-stream" in headers


def test_nginx_writes_the_snippet_to_the_target_dir(tmp_path):
    site = context(tmp_path)
    get_target("nginx").emit(site)
    conf = (site.target_dir / "ghr-pypi.conf").read_text()
    assert str(site.out_dir.resolve()) in conf
    assert "try_files" in conf
    assert "default_type application/octet-stream;" in conf


def test_nginx_publishes_nothing(tmp_path):
    # The whole point of target_dir: a server snippet under out_dir would be
    # served to anyone who can reach the index.
    site = context(tmp_path)
    get_target("nginx").emit(site)
    assert list(site.out_dir.iterdir()) == []


def test_nginx_does_not_override_the_mime_map(tmp_path):
    # A `types { }` block in a server context REPLACES the inherited map,
    # which would break the content type of every .html and .json we emit.
    site = context(tmp_path)
    get_target("nginx").emit(site)
    assert "types {" not in (site.target_dir / "ghr-pypi.conf").read_text()
```

- [ ] **Step 2: Run and confirm they fail** (the stubs emit nothing).

- [ ] **Step 3: `src/ghr_pypi/targets/cloudflare.py`:**

```python
"""Cloudflare Pages: cache headers for the generated site."""

from ghr_pypi.targets import SiteContext

_PREAMBLE = "# Generated by ghr-pypi. Cloudflare Pages reads this at deploy time.\n"


class CloudflareTarget:
    """Writes ``_headers`` next to the index."""

    name = "cloudflare"

    def emit(self, site: SiteContext) -> None:
        """Write ``out_dir/_headers``.

        The index changes on every release, so it revalidates quickly.
        Mirrored files never change under a given filename, so they are
        immutable — and ``.metadata`` gets an explicit type, because Pages
        guesses wrong for an extension it does not know.
        """
        lines = [
            _PREAMBLE,
            "\n/simple/*\n",
            "  Cache-Control: public, max-age=300, must-revalidate\n",
        ]
        if site.assets == "mirror":
            lines += [
                "\n/files/*\n",
                "  Cache-Control: public, max-age=31536000, immutable\n",
                "\n/files/*.metadata\n",
                "  Content-Type: application/octet-stream\n",
            ]
        (site.out_dir / "_headers").write_text("".join(lines), encoding="utf-8")
```

- [ ] **Step 4: `src/ghr_pypi/targets/nginx.py`:**

```python
"""nginx: a server-block snippet for the generated site."""

from ghr_pypi.targets import SiteContext

_SNIPPET = """\
# Generated by ghr-pypi. Include this inside a server block; it is not a
# complete nginx.conf:
#
#   server {{
#       listen 443 ssl;
#       server_name pypi.example.com;
#       include /etc/nginx/ghr-pypi.conf;
#   }}

root {root};
autoindex off;

# `.metadata` has no mime.types entry, so nginx would serve it as text/plain.
# Setting the fallback fixes that without a `types {{ }}` block, which in this
# context would REPLACE the inherited map and break every .html and .json.
default_type application/octet-stream;

# PEP 503 asks installers for directory URLs; serve the generated index.
location / {{
    try_files $uri $uri/index.html =404;
}}
"""


class NginxTarget:
    """Writes ``ghr-pypi.conf`` into the operator directory."""

    name = "nginx"

    def emit(self, site: SiteContext) -> None:
        """Write ``target_dir/ghr-pypi.conf``.

        Deliberately not ``out_dir``: a server configuration written into the
        published tree would be served to anyone who can reach the index.
        """
        (site.target_dir / "ghr-pypi.conf").write_text(
            _SNIPPET.format(root=site.out_dir.resolve()), encoding="utf-8"
        )
```

- [ ] **Step 5: Verify.**

Run: `uv run pytest tests/test_targets.py -q` → all pass
Run: `just test` → report the count
Run: `just fix`, then `just check-types` → clean

- [ ] **Step 6: Sanity-check the nginx snippet.** If `nginx` is available (`command -v nginx`), write a minimal wrapper `nginx.conf` that includes the generated snippet inside a `server` block and run `nginx -t -c <path>`. Report the result. If nginx is not installed, say so — do not fake it.

*(Driver checkpoint: commit as "Add the cloudflare and nginx targets")*

---

### Task 4: CLI wiring — `target` config key, `--target`, `--target-out`

**Goal:** A build selects and runs a target.

**Files:**
- Modify: `src/ghr_pypi/config.py`, `src/ghr_pypi/cli.py`
- Modify: `tests/test_config.py`, `tests/test_cli.py`

**Acceptance Criteria:**
- [ ] `target` loads from config, defaults to `"static"`, must be a string
- [ ] `--target` sets it; `--target` with `--config` raises
- [ ] `--target-out` defaults to `Path(".")` and is allowed with `--config`
- [ ] An unknown target name exits 1 listing the registered names
- [ ] `target_dir` is created if missing
- [ ] The target runs **after** `write_site`
- [ ] Deleting the `emit` call fails a test

**Verify:** `just test` → all pass (report the count)

**Steps:**

- [ ] **Step 1: Write the failing tests.** In `tests/test_config.py`:

```python
def test_target_defaults_to_static(tmp_path):
    assert load(write(tmp_path, "repositories: [a/b]\n")).target == "static"


def test_target_loads(tmp_path):
    assert load(write(tmp_path, "repositories: [a/b]\ntarget: nginx\n")).target == "nginx"


def test_target_must_be_a_string(tmp_path):
    with pytest.raises(ConfigError, match="'target' must be a string"):
        load(write(tmp_path, "repositories: [a/b]\ntarget: 5\n"))
```

In `tests/test_cli.py`:

```python
def test_resolve_rejects_target_beside_a_config(tmp_path):
    cfg_file = config_file(tmp_path, "repositories: [a/one]\n")
    with pytest.raises(ConfigError, match="set 'target' in the config file"):
        resolve(config=cfg_file, target="nginx")


def test_cli_unknown_target_fails_before_the_build(tmp_path, monkeypatch):
    def explode(repo, token):
        raise AssertionError("the build must not start with an unknown target")

    monkeypatch.setattr(index, "fetch_releases", explode)
    result = runner.invoke(
        app,
        ["index", "a/b", "--out", str(tmp_path), "--token", "x", "--target", "nope"],
    )
    assert result.exit_code == 1
    assert "unknown target 'nope'; available: " in all_output(result)
    assert not (tmp_path / "simple").exists()


def test_cli_runs_the_selected_target(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "fetch_releases", fetch_stub(FIXTURE_RELEASES))
    monkeypatch.setattr(index, "hash_url", lambda url: "cafef00d")
    result = runner.invoke(
        app,
        [
            "index", "a/b",
            "--out", str(tmp_path / "site"),
            "--token", "x",
            "--target", "cloudflare",
        ],
    )
    assert result.exit_code == 0, all_output(result)
    assert (tmp_path / "site" / "_headers").exists()


def test_cli_target_out_receives_operator_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "fetch_releases", fetch_stub(FIXTURE_RELEASES))
    monkeypatch.setattr(index, "hash_url", lambda url: "cafef00d")
    ops = tmp_path / "ops"
    result = runner.invoke(
        app,
        [
            "index", "a/b",
            "--out", str(tmp_path / "site"),
            "--token", "x",
            "--target", "nginx",
            "--target-out", str(ops),
        ],
    )
    assert result.exit_code == 0, all_output(result)
    assert (ops / "ghr-pypi.conf").exists()
    assert not (tmp_path / "site" / "ghr-pypi.conf").exists()


def test_cli_default_target_writes_no_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "fetch_releases", fetch_stub(FIXTURE_RELEASES))
    monkeypatch.setattr(index, "hash_url", lambda url: "cafef00d")
    result = runner.invoke(
        app, ["index", "a/b", "--out", str(tmp_path), "--token", "x"]
    )
    assert result.exit_code == 0, all_output(result)
    assert not (tmp_path / "_headers").exists()
```

Update the `resolve()` helper in `tests/test_cli.py` to accept and forward a
`target` keyword.

- [ ] **Step 2: Run and confirm they fail.**

- [ ] **Step 3: `config.py`.** Add `"target"` to `_KNOWN_KEYS`, add `target: str = "static"` to `Config`, and in `load`:

```python
    target = raw.get("target", "static")
    if not isinstance(target, str):
        raise ConfigError(f"{path}: 'target' must be a string")
```

Pass `target=target` in the `Config(...)` construction. **Do not validate the name against the registry here** — `config` must not import `targets`.

- [ ] **Step 4: `cli.py`.** Add `from ghr_pypi.targets import SiteContext, get_target`.

Give `_resolve_config` a keyword-only `target: str | None = None`. In the `--config` branch, beside the existing `--mirror` check:

```python
        if target is not None:
            raise ConfigError("with --config, set 'target' in the config file")
```

In the no-config branch's `Config(...)`, pass `target=target or "static"`.

Add two options to `build_index`, after `--mirror`:

```python
    target: Annotated[
        str | None,
        typer.Option(
            "--target",
            help="Deployment target emitting host artifacts "
            "(with --config, set 'target' in the config file instead)",
        ),
    ] = None,
    target_out: Annotated[
        Path,
        typer.Option(
            "--target-out",
            help="Directory for operator artifacts that must NOT be published",
        ),
    ] = Path("."),
```

Pass `target=target` into the `_resolve_config` call.

**Resolve the target early, emit late.** Immediately after `_resolve_config` succeeds — before any network request — do:

```python
    try:
        selected = get_target(cfg.target)
    except ValueError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from error
```

A misspelled target name must fail before the build, not after it: resolving late would download every asset and write the whole site only to die on a typo. Then, **after** the `write_site(...)` call and before the final `typer.echo`:

```python
    target_out.mkdir(parents=True, exist_ok=True)
    selected.emit(
        SiteContext(
            projects=projects,
            out_dir=out,
            target_dir=target_out,
            title=cfg.title,
            index_url=index_url,
            assets=cfg.assets,
            formats=cfg.formats,
        )
    )
```

- [ ] **Step 5: Verify.**

Run: `just test` → report the count
Run: `just fix`, then `just check-types` → clean

- [ ] **Step 6: Mutation checks.** One at a time, reverting each with `Edit`:

1. Delete the `selected.emit(...)` call → confirm `test_cli_runs_the_selected_target` and `test_cli_target_out_receives_operator_artifacts` FAIL.
2. Change `target_dir=target_out` to `target_dir=out` → confirm `test_cli_target_out_receives_operator_artifacts` FAILS. This is the publish-your-server-config bug; it must be caught.
3. Move the `emit` call *before* `write_site` → report whether anything fails. If nothing does, say so plainly rather than claiming coverage.

Report all three.

*(Driver checkpoint: commit as "Wire deployment targets into the CLI")*

---

### Task 5: Docs and full gate

**Goal:** The new keys, options and plugin interface are documented; the gate is green.

**Files:**
- Modify: `doc/source/reference/configuration.rst`, `doc/source/reference/cli.rst`, `doc/source/reference/index.rst`, `doc/source/how-to/index.rst`, `doc/source/changelog.rst`, `README.md`, `direction.md`
- Create: `doc/source/reference/targets.rst`, `doc/source/how-to/write-a-target.rst`

**Acceptance Criteria:**
- [ ] `configuration.rst` documents `assets` and `target`, the `mirror` deprecation, and every new error message verbatim
- [ ] Every existing `mirror: true` example in the docs uses `assets: mirror`, with the deprecation noted once
- [ ] `cli.rst` documents `--target` and `--target-out`, including that `--target` conflicts with `--config` and `--target-out` does not
- [ ] `reference/targets.rst` documents the protocol, `SiteContext`, the `out_dir`/`target_dir` rule and the entry point
- [ ] A how-to walks through writing and registering a custom target
- [ ] The changelog records the deprecation
- [ ] `just check-all` → exit 0 and a warning-free strict HTML build

**Verify:** `just check-all > /tmp/gate.log 2>&1; echo EXIT=$?` → `EXIT=0`

**Steps:**

- [ ] **Step 1: Find every `mirror` mention.**

```bash
grep -rn "mirror" --include='*.rst' --include='*.md' doc/source README.md
```

Every config example using `mirror: true` becomes `assets: mirror`. Prose about "mirror mode" stays — the *mode* is still called mirroring; only the key changed. `config-mirror` is an existing `:ref:` label used elsewhere: rename it to `config-assets` and fix every reference, or keep the old label as well so nothing breaks. Report which you did.

- [ ] **Step 2: `configuration.rst`.** Replace the `mirror` key section with an `assets` section in the page's existing per-key format: values `link` (default) and `mirror`, what each does, and that `mirror: true` still loads with a deprecation warning and cannot be combined with `assets`. Add a `target` section: any registered name, default `static`, that names come from built-ins plus plugins so the valid set is not fixed, and a pointer to `reference/targets.rst`. Update the summary table. Add these to the validation errors section, **verbatim from `src/ghr_pypi/config.py`**:
  - `{path}: set either 'assets' or the deprecated 'mirror', not both`
  - `{path}: 'assets' must be one of link, mirror, got {value!r}`
  - `{path}: 'missing_digest' has no effect when 'assets' is mirror` (the wording changed — the old entry said `when 'mirror' is enabled`)
  - `{path}: 'target' must be a string`

- [ ] **Step 3: `cli.rst`.** Document `--target` and `--target-out` in the `index` options list, note `--mirror` is now shorthand for `assets: mirror`, and add `error: unknown target '<name>'; available: ...` to the exit-1 list. State plainly that `--target-out` is a path like `--out` and is therefore allowed with `--config`, while `--target` is a behavioral switch and is not.

- [ ] **Step 4: Create `doc/source/reference/targets.rst`.** Reference voice, stating facts:

  - What a target is and the one thing it does not do (rewrite URLs — that is `assets`).
  - The `Target` protocol and `SiteContext`, generated with `automodule`/`autoclass` against `ghr_pypi.targets` so the docstrings are the single source.
  - **The `out_dir` versus `target_dir` rule**, with the reason: anything under `out_dir` is published.
  - The built-in targets and exactly what each writes.
  - The `ghr_pypi.targets` entry point: the value must be a callable returning a `Target`, and a built-in wins a name collision with a warning.

  Add it to the toctree in `doc/source/reference/index.rst`.

- [ ] **Step 5: Create `doc/source/how-to/write-a-target.rst`**, titled as a question like its siblings. A complete worked example: a small class with `name` and `emit`, the `[project.entry-points."ghr_pypi.targets"]` table in `pyproject.toml`, installing it, and `ghr-pypi index --target yourname`. Say which directory to write to and why. Add it to the toctree in `doc/source/how-to/index.rst`.

- [ ] **Step 6: `README.md`.** Update any `mirror: true` in the config example to `assets: mirror`, and add one line to the feature list about deployment targets. Keep lines short — that region is MyST-included into `doc/source/index.rst`.

- [ ] **Step 7: `doc/source/changelog.rst`.** Add to the unreleased section, matching the file's bullet style:

```rst
* Deployment targets: ``target: cloudflare`` writes Cloudflare Pages cache
  headers and ``target: nginx`` writes a server snippet, through a documented
  ``ghr_pypi.targets`` plugin interface.
* **Deprecated:** the ``mirror`` key is now ``assets: link | mirror``.
  ``mirror: true`` still works and warns.
```

- [ ] **Step 8: `direction.md`.** The "Target-specific artifact generation" item in the Scoping read section is now partly done — narrow it to what remains (the Worker redirector, webhook receiver, `_redirects`) rather than deleting it. The file is gitignored; verify by reading it back.

- [ ] **Step 9: Gate.**

```
just fix
just test
just check-all > /tmp/gate.log 2>&1; echo EXIT=$?
uv run --no-default-groups --group docs sphinx-build -b html -a -E -n ./doc/source /tmp/dt-docs
```

`EXIT=0` and a warning-free strict build. Fix any linkcheck failure by correcting the URL — never by disabling linkcheck. The justfile invokes linkcheck with a leading `-`, so link failures do not fail the gate: read `doc/build/output.txt` and report it.

Then confirm nothing stale survives:

```bash
grep -rn "mirror: true\|config-mirror" --include='*.rst' --include='*.md' doc/source README.md
```

Judge every hit and report what you left and why.

*(Driver checkpoint: commit as "Document deployment targets")*

---

## After the plan

Driver: commit the checkpoints. Sub-project 2 adds `assets: redirect` and the
Worker, and plugs into the `Target` interface built here.
