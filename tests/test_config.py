from pathlib import Path

import pytest

from ghr_pypi.config import Config, ConfigError, Filters, is_pattern, load


def write(tmp_path: Path, text: str) -> Path:
    cfg = tmp_path / "index.yml"
    cfg.write_text(text)
    return cfg


def test_load_full(tmp_path):
    (tmp_path / "tpl").mkdir()
    cfg = load(
        write(
            tmp_path,
            """
repositories:
  - bckohan/ghr-pypi
  - someorg/other-project
templates: ./tpl
title: My Package Index
url: https://example.github.io/idx/
""",
        )
    )
    assert cfg == Config(
        repositories=("bckohan/ghr-pypi", "someorg/other-project"),
        templates=(tmp_path / "tpl").resolve(),
        title="My Package Index",
        url="https://example.github.io/idx/",
    )


def test_load_minimal_defaults(tmp_path):
    cfg = load(write(tmp_path, "repositories: [a/b]\n"))
    assert cfg.repositories == ("a/b",)
    assert cfg.templates is None
    assert cfg.title == "Package index"
    assert cfg.url is None


def test_repositories_may_be_omitted(tmp_path):
    cfg = load(write(tmp_path, "title: Just a title\n"))
    assert cfg.repositories == ()
    assert cfg.title == "Just a title"


def test_repositories_explicit_null_treated_as_omitted(tmp_path):
    cfg = load(write(tmp_path, "repositories:\n"))
    assert cfg.repositories == ()


def test_templates_relative_to_config_dir(tmp_path, monkeypatch):
    (tmp_path / "tpl").mkdir()
    monkeypatch.chdir(tmp_path.parent)  # CWD != config dir
    cfg = load(write(tmp_path, "repositories: [a/b]\ntemplates: tpl\n"))
    assert cfg.templates == (tmp_path / "tpl").resolve()


@pytest.mark.parametrize(
    "text,match",
    [
        ("- just\n- a list\n", "top level must be a mapping"),
        ("repositories: [a/b]\nbogus: 1\n", "unknown key"),
        ("repositories: []\n", "'repositories' must be a non-empty list"),
        ("repositories: notalist\n", "'repositories' must be a non-empty list"),
        ("repositories: [noslash]\n", "is not OWNER/NAME"),
        ("repositories: [a/b/c]\n", "is not OWNER/NAME"),
        ("repositories: [/b]\n", "is not OWNER/NAME"),
        ("repositories: [123]\n", "is not OWNER/NAME"),
        ("repositories: [a/b, a/b]\n", "contains duplicates"),
        ("repositories: [A/b, a/b]\n", "contains duplicates"),
        ("repositories: [a/b]\ntemplates: 123\n", "'templates' must be a string path"),
        (
            "repositories: [a/b]\ntemplates: [a, b]\n",
            "'templates' must be a string path",
        ),
        (
            "repositories: [a/b]\ntemplates: ./missing\n",
            "templates directory not found",
        ),
        ("repositories: [a/b]\nurl: http://insecure.example\n", "'url' must be https"),
        ("repositories: [a/b]\nurl: true\n", "'url' must be a string"),
        ("repositories: [a/b]\ntitle: [not, a, string]\n", "'title' must be a string"),
        ("repositories: [a/b]\nurl: [::bad yaml::\n", "invalid YAML"),
        (
            "repositories: [a/b]\nmissing_digest: always\n",
            "must be one of download, no-fragment, omit",
        ),
        (
            "repositories: [a/b]\nmissing_digest: true\n",
            "'missing_digest' must be one of",
        ),
        ("repositories: [a/b]\nformats: []\n", "'formats' must be a non-empty list"),
        ("repositories: [a/b]\nformats: html\n", "'formats' must be a non-empty list"),
        (
            "repositories: [a/b]\nformats: [xml]\n",
            "'formats' entries must be html or json, got 'xml'",
        ),
        (
            "repositories: [a/b]\nformats: [html, html]\n",
            "'formats' contains duplicates",
        ),
        ("repositories: [a/b]\nmirror: maybe\n", "'mirror' must be true or false"),
        ("repositories: [a/b]\nmirror: 1\n", "'mirror' must be true or false"),
        ("repositories: [a/b]\nmetadata: maybe\n", "'metadata' must be true or false"),
        ("repositories: [a/b]\nmetadata: 1\n", "'metadata' must be true or false"),
        (
            "repositories: [a/b]\nmirror: true\nmissing_digest: download\n",
            "'missing_digest' has no effect when 'assets' is mirror",
        ),
        ("repositories: [a/b]\nyanked: [1.0.0]\n", "'yanked' must be a mapping"),
        ("repositories: [a/b]\nyanked: nope\n", "'yanked' must be a mapping"),
        (
            "repositories: [a/b]\nyanked:\n  1: {'1.0': true}\n",
            "'yanked' project keys must be strings, got 1",
        ),
        (
            "repositories: [a/b]\nyanked:\n  demo-lib: [1.0.0]\n",
            "'yanked.demo-lib' must be a mapping of version to reason",
        ),
        (
            "repositories: [a/b]\nyanked:\n  demo-lib: 1.0.0\n",
            "'yanked.demo-lib' must be a mapping of version to reason",
        ),
        (
            "repositories: [a/b]\nyanked:\n  demo-lib:\n    1.0: true\n",
            "'yanked.demo-lib' version keys must be quoted strings, got 1.0",
        ),
        (
            "repositories: [a/b]\nyanked:\n  demo-lib:\n    1: true\n",
            "'yanked.demo-lib' version keys must be quoted strings, got 1",
        ),
        (
            "repositories: [a/b]\nyanked:\n  demo-lib:\n    '1.0': false\n",
            "'yanked.demo-lib.1.0' is false; remove the entry to un-yank",
        ),
        (
            "repositories: [a/b]\nyanked:\n  demo-lib:\n    '1.0': ''\n",
            "'yanked.demo-lib.1.0' has an empty reason; use true for a yank",
        ),
        (
            "repositories: [a/b]\nyanked:\n  demo-lib:\n    '1.0': 3\n",
            "'yanked.demo-lib.1.0' must be a reason string or true",
        ),
        (
            "repositories: [a/b]\nyanked:\n  demo-lib:\n    '1.0': [why]\n",
            "'yanked.demo-lib.1.0' must be a reason string or true",
        ),
        (
            "repositories: [a/b]\nyanked:\n  demo-lib:\n    '1.0': a\n    '1.0.0': b\n",
            "'yanked.demo-lib' has two entries for version '1.0.0'",
        ),
        (
            "repositories: [a/b]\nyanked:\n"
            "  Demo_Lib:\n    '1.0': true\n  demo-lib:\n    '2.0': true\n",
            "'yanked' has two entries for project 'demo-lib'",
        ),
        ("repositories: [a/b]\nexclude: [1.0.0]\n", "'exclude' must be a mapping"),
        ("repositories: [a/b]\nexclude: nope\n", "'exclude' must be a mapping"),
        (
            "repositories: [a/b]\nexclude:\n  1: ['1.0']\n",
            "'exclude' project keys must be strings, got 1",
        ),
        (
            "repositories: [a/b]\nexclude:\n  demo-lib: '1.0'\n",
            "'exclude.demo-lib' must be a list of version strings",
        ),
        (
            "repositories: [a/b]\nexclude:\n  demo-lib: {'1.0': true}\n",
            "'exclude.demo-lib' must be a list of version strings",
        ),
        (
            "repositories: [a/b]\nexclude:\n  demo-lib: [1.0]\n",
            "'exclude.demo-lib' versions must be quoted strings, got 1.0",
        ),
        (
            "repositories: [a/b]\nexclude:\n  demo-lib: [true]\n",
            "'exclude.demo-lib' versions must be quoted strings, got True",
        ),
        (
            "repositories: [a/b]\nexclude:\n  demo-lib: ['1.0', '1.0.0']\n",
            "'exclude.demo-lib' has two entries for version '1.0.0'",
        ),
        (
            "repositories: [a/b]\nexclude:\n  Demo.Lib: ['1.0']\n  demo-lib: ['2.0']\n",
            "'exclude' has two entries for project 'demo-lib'",
        ),
    ],
)
def test_load_errors(tmp_path, text, match):
    with pytest.raises(ConfigError, match=match):
        load(write(tmp_path, text))


@pytest.mark.parametrize("value", ["download", "no-fragment", "omit"])
def test_missing_digest_values(tmp_path, value):
    cfg = load(write(tmp_path, f"repositories: [a/b]\nmissing_digest: {value}\n"))
    assert cfg.missing_digest == value


def test_missing_digest_default(tmp_path):
    assert load(write(tmp_path, "repositories: [a/b]\n")).missing_digest == "download"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("[html]", ("html",)),
        ("[json]", ("json",)),
        ("[html, json]", ("html", "json")),
        ("[json, html]", ("json", "html")),
    ],
)
def test_formats_values(tmp_path, value, expected):
    cfg = load(write(tmp_path, f"repositories: [a/b]\nformats: {value}\n"))
    assert cfg.formats == expected


def test_formats_default(tmp_path):
    assert load(write(tmp_path, "repositories: [a/b]\n")).formats == ("html", "json")


@pytest.mark.parametrize("value,expected", [("true", True), ("false", False)])
def test_metadata_values(tmp_path, value, expected):
    cfg = load(write(tmp_path, f"repositories: [a/b]\nmetadata: {value}\n"))
    assert cfg.metadata is expected


def test_metadata_default(tmp_path):
    assert load(write(tmp_path, "repositories: [a/b]\n")).metadata is True


def test_mirror_allows_missing_digest_when_off(tmp_path):
    cfg = load(
        write(tmp_path, "repositories: [a/b]\nmirror: false\nmissing_digest: omit\n")
    )
    assert cfg.assets == "link" and cfg.missing_digest == "omit"


def test_assets_defaults_to_link(tmp_path):
    assert load(write(tmp_path, "repositories: [a/b]\n")).assets == "link"


def test_assets_mirror_loads(tmp_path):
    assert (
        load(write(tmp_path, "repositories: [a/b]\nassets: mirror\n")).assets
        == "mirror"
    )


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


def test_mirror_null_value_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="'mirror' must be true or false"):
        load(write(tmp_path, "repositories: [a/b]\nmirror:\n"))


def test_assets_null_value_is_rejected(tmp_path):
    with pytest.raises(
        ConfigError, match="'assets' must be one of link, mirror, got None"
    ):
        load(write(tmp_path, "repositories: [a/b]\nassets:\n"))


def test_mirror_and_assets_together_are_rejected_even_when_assets_is_null(tmp_path):
    # regression: presence must be checked with `"assets" in raw`, not
    # `raw.get("assets") is not None` -- the latter treats an explicit null
    # as absent and lets 'mirror' silently win the conflict it should raise on
    with pytest.raises(ConfigError, match="not both"):
        load(write(tmp_path, "repositories: [a/b]\nassets:\nmirror: true\n"))


def test_missing_digest_still_rejected_under_mirroring(tmp_path):
    with pytest.raises(ConfigError, match="'missing_digest' has no effect"):
        load(
            write(
                tmp_path, "repositories: [a/b]\nassets: mirror\nmissing_digest: omit\n"
            )
        )


def test_filters_default_to_empty(tmp_path):
    cfg = load(write(tmp_path, "repositories: [a/b]\n"))
    assert cfg.filters == Filters()
    assert cfg.filters.yanked == {}
    assert cfg.filters.exclude == {}


def test_load_yanked_and_exclude(tmp_path):
    cfg = load(
        write(
            tmp_path,
            """
repositories: [a/b]
yanked:
  Demo_Lib:
    "1.0.0": sdist built from a dirty tree
    "1.0.1": true
  other-lib:
    "2.0": true
exclude:
  Demo.Lib:
    - "0.9.0"
    - "0.9.1"
""",
        )
    )
    assert cfg.filters.yanked == {
        "demo-lib": {"1.0.0": "sdist built from a dirty tree", "1.0.1": True},
        "other-lib": {"2.0": True},
    }
    assert cfg.filters.exclude == {"demo-lib": ("0.9.0", "0.9.1")}
    assert cfg.filters.yank_reason("demo-lib", "1.0.0") == (
        "sdist built from a dirty tree"
    )
    assert cfg.filters.is_excluded("demo-lib", "0.9.1") is True


def test_load_empty_yanked_and_exclude_mappings(tmp_path):
    cfg = load(
        write(tmp_path, "repositories: [a/b]\nyanked: {}\nexclude:\n  demo-lib: []\n")
    )
    assert cfg.filters.yanked == {}
    assert cfg.filters.exclude == {"demo-lib": ()}


def test_load_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="cannot read config file"):
        load(tmp_path / "nope.yml")


def test_load_non_utf8(tmp_path):
    cfg = tmp_path / "index.yml"
    cfg.write_bytes(b"\xff\xfe repositories: [a/b]")
    with pytest.raises(ConfigError, match="not valid UTF-8"):
        load(cfg)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("plain", False),
        ("has-dash", False),
        ("*", True),
        ("lib-*", True),
        ("lib-?", True),
        ("lib-[ab]", True),
    ],
)
def test_is_pattern(name, expected):
    assert is_pattern(name) is expected


def test_repositories_accept_patterns(tmp_path):
    cfg = write(tmp_path, "repositories: [yourorg/*, yourorg/lib-*, other/one]\n")
    assert load(cfg).repositories == ("yourorg/*", "yourorg/lib-*", "other/one")


@pytest.mark.parametrize("owner", ["*", "a-[x]"])
def test_repositories_reject_a_pattern_in_the_owner(tmp_path, owner):
    cfg = write(tmp_path, f"repositories: ['{owner}/thing']\n")
    with pytest.raises(ConfigError, match="may not use a pattern in the owner"):
        load(cfg)


def test_exclude_repositories_defaults_to_empty(tmp_path):
    cfg = write(tmp_path, "repositories: [a/b]\n")
    assert load(cfg).exclude_repositories == ()


def test_exclude_repositories_loads(tmp_path):
    cfg = write(tmp_path, "repositories: [a/*]\nexclude_repositories: [a/secret-*]\n")
    assert load(cfg).exclude_repositories == ("a/secret-*",)


def test_exclude_repositories_explicit_null_treated_as_omitted(tmp_path):
    cfg = write(tmp_path, "repositories: [a/b]\nexclude_repositories:\n")
    assert load(cfg).exclude_repositories == ()


@pytest.mark.parametrize(
    "body,message",
    [
        (
            "repositories: [a/b]\nexclude_repositories: nope\n",
            "'exclude_repositories' must be a list",
        ),
        (
            "repositories: [a/b]\nexclude_repositories: [5]\n",
            "is not OWNER/NAME",
        ),
        (
            "repositories: [a/b]\nexclude_repositories: ['*/x']\n",
            "may not use a pattern in the owner",
        ),
        (
            "repositories: [a/b]\nexclude_repositories: false\n",
            "'exclude_repositories' must be a list",
        ),
        (
            "repositories: [a/b]\nexclude_repositories: 0\n",
            "'exclude_repositories' must be a list",
        ),
        (
            "repositories: [a/b]\nexclude_repositories: ''\n",
            "'exclude_repositories' must be a list",
        ),
        (
            "repositories: [a/b]\nexclude_repositories: {}\n",
            "'exclude_repositories' must be a list",
        ),
    ],
)
def test_exclude_repositories_errors(tmp_path, body, message):
    with pytest.raises(ConfigError, match=message):
        load(write(tmp_path, body))


def test_target_defaults_to_static(tmp_path):
    assert load(write(tmp_path, "repositories: [a/b]\n")).target == "static"


def test_target_loads(tmp_path):
    assert (
        load(write(tmp_path, "repositories: [a/b]\ntarget: nginx\n")).target == "nginx"
    )


def test_target_must_be_a_string(tmp_path):
    with pytest.raises(ConfigError, match="'target' must be a string"):
        load(write(tmp_path, "repositories: [a/b]\ntarget: 5\n"))
