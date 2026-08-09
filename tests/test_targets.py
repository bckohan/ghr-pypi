"""Tests for the pluggable deployment target registry."""

import dataclasses
import re
from collections.abc import Sequence

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
