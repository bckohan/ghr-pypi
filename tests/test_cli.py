import json
import urllib.error
import zipfile

import pytest
from typer.testing import CliRunner

from ghr_pypi import index
from ghr_pypi.cli import _expand_patterns, _resolve_config, app
from ghr_pypi.config import ConfigError
from ghr_pypi.targets import get_target
from tests.test_index import FILTER_RELEASES, FIXTURE_RELEASES

runner = CliRunner()


@pytest.fixture(autouse=True, scope="module")
def _no_github_repository():
    """Unset GITHUB_REPOSITORY for this module.

    GitHub Actions sets it for every step, including the test run, and the CLI
    derives the Pages URL from it. An ambient value would silently supply a URL
    to the tests that assert there is none — notably
    ``test_cli_config_merges_repositories``, whose
    ``"--extra-index-url" not in landing`` would fail in CI only.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("GITHUB_REPOSITORY", raising=False)
        yield


def all_output(result):
    """stdout+stderr across click versions (mix_stderr removed in click 8.2)."""
    try:
        return result.output + (result.stderr or "")
    except (ValueError, AttributeError):
        return result.output


def fetch_stub(releases):
    """fetch_releases fake returning fresh copies; the CLI tags releases in place."""
    return lambda repo, token: [dict(release) for release in releases]


def test_cli_requires_token(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    result = runner.invoke(app, ["index", "bckohan/ghr-pypi", "--out", str(tmp_path)])
    assert result.exit_code == 1
    assert "provide --token or set GITHUB_TOKEN" in all_output(result)


def test_cli_fails_with_no_packages(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "fetch_releases", lambda repo, token: [])
    result = runner.invoke(
        app, ["index", "bckohan/ghr-pypi", "--out", str(tmp_path), "--token", "x"]
    )
    assert result.exit_code == 1
    assert "no package assets" in all_output(result)


def test_cli_reports_api_failure(tmp_path, monkeypatch):
    import urllib.error

    def boom(repo, token):
        raise urllib.error.URLError("nope")

    monkeypatch.setattr(index, "fetch_releases", boom)
    result = runner.invoke(
        app, ["index", "bckohan/ghr-pypi", "--out", str(tmp_path), "--token", "x"]
    )
    assert result.exit_code == 1
    assert "GitHub API request for bckohan/ghr-pypi failed" in all_output(result)


def test_cli_writes_site(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "fetch_releases", fetch_stub(FIXTURE_RELEASES))
    monkeypatch.setattr(index, "hash_url", lambda url: "cafef00d")
    result = runner.invoke(
        app, ["index", "bckohan/ghr-pypi", "--out", str(tmp_path), "--token", "x"]
    )
    assert result.exit_code == 0, all_output(result)
    assert "wrote index for 2 project(s)" in result.output
    assert (tmp_path / "simple" / "ghr-pypi-demo-lib" / "index.html").exists()


def test_cli_single_repository_advertises_the_index(tmp_path, monkeypatch):
    # the config test only asserts the install example is absent; this proves
    # it renders at all when a url is derived
    monkeypatch.setattr(index, "fetch_releases", fetch_stub(FIXTURE_RELEASES))
    monkeypatch.setattr(index, "hash_url", lambda url: "cafef00d")
    out = tmp_path / "site"
    result = runner.invoke(
        app, ["index", "bckohan/ghr-pypi", "--out", str(out), "--token", "x"]
    )
    assert result.exit_code == 0, all_output(result)
    landing = (out / "index.html").read_text()
    assert (
        "--extra-index-url https://bckohan.github.io/ghr-pypi/simple/ PACKAGE"
        in landing
    )


def test_cli_token_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setattr(index, "fetch_releases", fetch_stub(FIXTURE_RELEASES))
    monkeypatch.setattr(index, "hash_url", lambda url: "cafef00d")
    result = runner.invoke(app, ["index", "bckohan/ghr-pypi", "--out", str(tmp_path)])
    assert result.exit_code == 0, all_output(result)
    assert (tmp_path / "simple" / "index.html").exists()


def config_file(tmp_path, text):
    cfg = tmp_path / "index.yml"
    cfg.write_text(text)
    return cfg


def resolve(repos=None, config=None, *, mirror=False, env_repo=None, target=None):
    return _resolve_config(
        repos, config, mirror=mirror, env_repo=env_repo, target=target
    )


def test_resolve_needs_a_source():
    with pytest.raises(ConfigError, match="provide REPO"):
        resolve()


def test_resolve_falls_back_to_the_environment():
    cfg = resolve(env_repo="bckohan/ghr-pypi")
    assert cfg.repositories == ("bckohan/ghr-pypi",)
    assert cfg.title == "bckohan/ghr-pypi package index"
    assert cfg.url == "https://bckohan.github.io/ghr-pypi/"


def test_resolve_empty_environment_variable_counts_as_unset():
    with pytest.raises(ConfigError, match="provide REPO"):
        resolve(env_repo="")


def test_resolve_several_repositories():
    cfg = resolve(["a/one", "a/two"])
    assert cfg.repositories == ("a/one", "a/two")
    assert cfg.title == "Package index"
    assert cfg.url is None


def test_resolve_derives_the_url_from_a_lone_argument():
    assert resolve(["a/one"]).url == "https://a.github.io/one/"


def test_resolve_url_comes_from_the_pages_host():
    cfg = resolve(["a/one", "a/two"], env_repo="host/site")
    assert cfg.repositories == ("a/one", "a/two")
    assert cfg.url == "https://host.github.io/site/"


def test_resolve_arguments_outrank_the_environment():
    cfg = resolve(["a/one"], env_repo="host/site")
    assert cfg.repositories == ("a/one",)
    assert cfg.url == "https://host.github.io/site/"


def test_resolve_rejects_a_bad_argument():
    with pytest.raises(ConfigError, match="repository 'nope' is not OWNER/NAME"):
        resolve(["nope"])


def test_resolve_rejects_a_bad_environment_value():
    with pytest.raises(ConfigError, match="GITHUB_REPOSITORY 'nope' is not OWNER/NAME"):
        resolve(env_repo="nope")


def test_resolve_rejects_duplicate_arguments():
    with pytest.raises(ConfigError, match="given more than once"):
        resolve(["a/one", "A/One"])


def test_resolve_does_not_deduplicate_across_sources():
    # by design: the argument and the environment are separate sources, so a
    # case-only difference between them is not a duplicate
    cfg = resolve(["Host/Site"], env_repo="host/site")
    assert cfg.repositories == ("Host/Site",)
    assert cfg.url == "https://host.github.io/site/"


def test_resolve_config_repositories_win(tmp_path):
    cfg_file = config_file(tmp_path, "repositories: [a/one]\n")
    cfg = resolve(config=cfg_file, env_repo="host/site")
    assert cfg.repositories == ("a/one",)
    assert cfg.url == "https://host.github.io/site/"


def test_resolve_config_never_derives_a_url_from_its_repositories(tmp_path):
    # a config omitting 'url' means "no install example"; the one repository
    # listed is not necessarily the one serving the site
    cfg_file = config_file(tmp_path, "repositories: [a/one]\n")
    assert resolve(config=cfg_file).url is None


def test_resolve_rejects_arguments_beside_a_config(tmp_path):
    cfg_file = config_file(tmp_path, "repositories: [a/one]\n")
    with pytest.raises(ConfigError, match="list repositories in the config file"):
        resolve(["a/two"], cfg_file)


def test_resolve_config_without_repositories_uses_the_environment(tmp_path):
    cfg_file = config_file(tmp_path, "title: Mine\n")
    cfg = resolve(config=cfg_file, env_repo="host/site")
    assert cfg.repositories == ("host/site",)
    assert cfg.title == "Mine"


def test_resolve_config_without_repositories_and_no_environment(tmp_path):
    cfg_file = config_file(tmp_path, "title: Mine\n")
    with pytest.raises(ConfigError, match="has no 'repositories'"):
        resolve(config=cfg_file)


def test_resolve_keeps_the_configured_url(tmp_path):
    cfg_file = config_file(
        tmp_path, "repositories: [a/one]\nurl: https://example.com/pypi/\n"
    )
    cfg = resolve(config=cfg_file, env_repo="host/site")
    assert cfg.url == "https://example.com/pypi/"


def test_resolve_rejects_mirror_beside_a_config(tmp_path):
    cfg_file = config_file(tmp_path, "repositories: [a/one]\n")
    with pytest.raises(ConfigError, match="set 'assets: mirror' in the config file"):
        resolve(config=cfg_file, mirror=True)


def test_resolve_mirror_flag_sets_the_asset_mode():
    assert resolve(["a/b"], mirror=True).assets == "mirror"
    assert resolve(["a/b"]).assets == "link"


def test_resolve_target_defaults_to_static():
    assert resolve(["a/b"]).target == "static"
    assert resolve(["a/b"], target="nginx").target == "nginx"


def test_resolve_rejects_target_beside_a_config(tmp_path):
    cfg_file = config_file(tmp_path, "repositories: [a/one]\n")
    with pytest.raises(ConfigError, match="set 'target' in the config file"):
        resolve(config=cfg_file, target="nginx")


def test_cli_defaults_repository_and_out(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "bckohan/ghr-pypi")
    monkeypatch.setattr(index, "fetch_releases", fetch_stub(FIXTURE_RELEASES))
    monkeypatch.setattr(index, "hash_url", lambda url: "cafef00d")
    result = runner.invoke(app, ["index", "--token", "x"])
    assert result.exit_code == 0, all_output(result)
    assert (tmp_path / "_site" / "simple" / "index.html").exists()


def test_cli_index_with_no_arguments_builds(tmp_path, monkeypatch):
    # pins the exact argv the release workflow runs: `ghr-pypi index` and
    # nothing else. Every other test passes at least one more argument, so
    # only this one catches `index` being turned into a help-printing group.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("GITHUB_REPOSITORY", "bckohan/ghr-pypi")
    monkeypatch.setattr(index, "fetch_releases", fetch_stub(FIXTURE_RELEASES))
    monkeypatch.setattr(index, "hash_url", lambda url: "cafef00d")
    result = runner.invoke(app, ["index"])
    assert result.exit_code == 0, all_output(result)
    assert (tmp_path / "_site" / "simple" / "index.html").exists()


def test_cli_config_merges_repositories(tmp_path, monkeypatch):
    second_repo_releases = [
        {
            "tag_name": "v1.0.0",
            "assets": [
                {
                    "name": "otherpkg-1.0.0-py3-none-any.whl",
                    "browser_download_url": "https://github.com/someorg/other/releases/download/v1.0.0/otherpkg-1.0.0-py3-none-any.whl",
                },
            ],
        },
    ]
    releases_by_repo = {
        "bckohan/ghr-pypi": FIXTURE_RELEASES,
        "someorg/other-project": second_repo_releases,
    }
    monkeypatch.setattr(
        index,
        "fetch_releases",
        lambda repo, token: fetch_stub(releases_by_repo[repo])(repo, token),
    )
    monkeypatch.setattr(index, "hash_url", lambda url: "cafef00d")
    cfg = config_file(
        tmp_path,
        """
repositories:
  - bckohan/ghr-pypi
  - someorg/other-project
title: Aggregated Index
""",
    )
    out = tmp_path / "site"
    result = runner.invoke(
        app, ["index", "--config", str(cfg), "--out", str(out), "--token", "x"]
    )
    assert result.exit_code == 0, all_output(result)
    assert "wrote index for 3 project(s)" in result.output
    assert (out / "simple" / "otherpkg" / "index.html").exists()
    assert (out / "simple" / "ghr-pypi-demo-lib" / "index.html").exists()
    landing = (out / "index.html").read_text()
    assert "Aggregated Index" in landing
    assert "--extra-index-url" not in landing  # no url in config


def test_cli_config_error(tmp_path):
    cfg = config_file(tmp_path, "repositories: []\n")
    result = runner.invoke(
        app, ["index", "--config", str(cfg), "--out", str(tmp_path), "--token", "x"]
    )
    assert result.exit_code == 1
    assert "'repositories' must be a non-empty list" in all_output(result)


def test_cli_config_url_failure_names_repo(tmp_path, monkeypatch):
    import urllib.error

    def boom(repo, token):
        if repo == "someorg/other-project":
            raise urllib.error.URLError("nope")
        return fetch_stub(FIXTURE_RELEASES)(repo, token)

    monkeypatch.setattr(index, "fetch_releases", boom)
    cfg = config_file(
        tmp_path,
        "repositories: [bckohan/ghr-pypi, someorg/other-project]\n",
    )
    result = runner.invoke(
        app, ["index", "--config", str(cfg), "--out", str(tmp_path), "--token", "x"]
    )
    assert result.exit_code == 1
    assert "GitHub API request for someorg/other-project failed" in all_output(result)


def test_cli_rejects_malformed_repo(tmp_path):
    result = runner.invoke(
        app, ["index", "foo", "--out", str(tmp_path), "--token", "x"]
    )
    assert result.exit_code == 1
    assert "is not OWNER/NAME" in all_output(result)


def test_cli_missing_digest_policy_flows_through(tmp_path, monkeypatch):
    legacy_release = [
        {
            "tag_name": "v1.0.0",
            "assets": [
                {
                    "name": "legacy-1.0.0-py3-none-any.whl",
                    "browser_download_url": "https://github.com/a/b/releases/download/v1.0.0/legacy-1.0.0-py3-none-any.whl",
                },
            ],
        },
    ]
    monkeypatch.setattr(index, "fetch_releases", lambda repo, token: legacy_release)

    def never_hash(url):
        raise AssertionError("hash_url called despite no-fragment policy")

    monkeypatch.setattr(index, "hash_url", never_hash)
    cfg = config_file(tmp_path, "repositories: [a/b]\nmissing_digest: no-fragment\n")
    out = tmp_path / "site"
    result = runner.invoke(
        app, ["index", "--config", str(cfg), "--out", str(out), "--token", "x"]
    )
    assert result.exit_code == 0, all_output(result)
    page = (out / "simple" / "legacy" / "index.html").read_text()
    assert "#sha256=" not in page


def test_cli_formats_json_only(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "fetch_releases", fetch_stub(FIXTURE_RELEASES))
    monkeypatch.setattr(index, "hash_url", lambda url: "cafef00d")
    cfg = config_file(tmp_path, "repositories: [a/b]\nformats: [json]\n")
    out = tmp_path / "site"
    result = runner.invoke(
        app, ["index", "--config", str(cfg), "--out", str(out), "--token", "x"]
    )
    assert result.exit_code == 0, all_output(result)
    assert (out / "simple" / "index.json").exists()
    assert not list(out.rglob("*.html"))


def test_cli_mirror_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "fetch_releases", fetch_stub(FIXTURE_RELEASES))
    calls = []

    def fake_mirror(projects, out_dir, token, **kwargs):
        calls.append((out_dir, token))
        for files in projects.values():
            for entry in files:
                entry["sha256"] = "cafef00d"
                entry["url"] = f"../../files/x/{entry['filename']}"

    monkeypatch.setattr(index, "mirror_files", fake_mirror)

    def never_hash(url):
        raise AssertionError("hash_url called despite mirror mode")

    monkeypatch.setattr(index, "hash_url", never_hash)
    out = tmp_path / "site"
    result = runner.invoke(
        app,
        [
            "index",
            "bckohan/ghr-pypi",
            "--out",
            str(out),
            "--token",
            "tok",
            "--mirror",
        ],
    )
    assert result.exit_code == 0, all_output(result)
    assert calls == [(out, "tok")]
    page = (out / "simple" / "ghr-pypi-demo-lib" / "index.html").read_text()
    assert "../../files/x/" in page


def test_cli_mirror_with_config_errors(tmp_path):
    cfg = config_file(tmp_path, "repositories: [a/b]\n")
    result = runner.invoke(
        app,
        [
            "index",
            "--config",
            str(cfg),
            "--out",
            str(tmp_path),
            "--token",
            "x",
            "--mirror",
        ],
    )
    assert result.exit_code == 1
    assert "with --config, set 'assets: mirror' in the config file" in all_output(
        result
    )


def test_cli_mirror_from_config(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "fetch_releases", fetch_stub(FIXTURE_RELEASES))
    calls = []

    def fake_mirror(projects, out_dir, token, **kwargs):
        calls.append(token)
        for files in projects.values():
            for entry in files:
                entry["sha256"] = "cafef00d"
                entry["url"] = f"../../files/x/{entry['filename']}"

    monkeypatch.setattr(index, "mirror_files", fake_mirror)
    cfg = config_file(tmp_path, "repositories: [a/b]\nmirror: true\n")
    result = runner.invoke(
        app,
        ["index", "--config", str(cfg), "--out", str(tmp_path / "s"), "--token", "tok"],
    )
    assert result.exit_code == 0, all_output(result)
    assert calls == ["tok"]


def test_cli_mirror_error_surfaces(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "fetch_releases", fetch_stub(FIXTURE_RELEASES))

    def boom(projects, out_dir, token, **kwargs):
        raise index.MirrorError("bad.whl: downloaded sha256 x does not match y")

    monkeypatch.setattr(index, "mirror_files", boom)
    result = runner.invoke(
        app,
        [
            "index",
            "bckohan/ghr-pypi",
            "--out",
            str(tmp_path),
            "--token",
            "t",
            "--mirror",
        ],
    )
    assert result.exit_code == 1
    assert "bad.whl" in all_output(result)


def test_cli_reports_asset_download_failure(tmp_path, monkeypatch):
    import urllib.error

    monkeypatch.setattr(index, "fetch_releases", fetch_stub(FIXTURE_RELEASES))

    def boom(url):
        raise urllib.error.URLError("asset gone")

    monkeypatch.setattr(index, "hash_url", boom)
    result = runner.invoke(
        app,
        ["index", "bckohan/ghr-pypi", "--out", str(tmp_path), "--token", "x"],
    )
    assert result.exit_code == 1
    assert "downloading a release asset failed" in all_output(result)


def test_cli_link_mode_metadata_warnings(tmp_path, monkeypatch):
    from tests.test_index import METADATA_RELEASE

    covered = [
        {
            "tag_name": "v1.0.0",
            "assets": [
                {
                    "name": "cov-1.0.0-py3-none-any.whl",
                    "browser_download_url": "https://github.com/a/covered/releases/download/v1.0.0/cov-1.0.0-py3-none-any.whl",
                    "digest": "sha256:aaaa",
                },
                {
                    "name": "cov-1.0.0-py3-none-any.whl.metadata",
                    "browser_download_url": "https://github.com/a/covered/releases/download/v1.0.0/cov-1.0.0-py3-none-any.whl.metadata",
                    "digest": "sha256:bbbb",
                },
            ],
        },
    ]
    releases_by_repo = {"a/covered": covered, "o/meta": METADATA_RELEASE}
    monkeypatch.setattr(
        index,
        "fetch_releases",
        lambda repo, token: fetch_stub(releases_by_repo[repo])(repo, token),
    )
    cfg = config_file(tmp_path, "repositories: [a/covered, o/meta]\n")
    result = runner.invoke(
        app,
        ["index", "--config", str(cfg), "--out", str(tmp_path / "s"), "--token", "x"],
    )
    assert result.exit_code == 0, all_output(result)
    err = all_output(result)
    assert "warning: o/meta: 1 of 3 wheels have no .metadata asset" in err
    assert "warning: a/covered" not in err


def test_cli_metadata_false_silences_warnings(tmp_path, monkeypatch):
    from tests.test_index import METADATA_RELEASE

    monkeypatch.setattr(index, "fetch_releases", fetch_stub(METADATA_RELEASE))
    cfg = config_file(tmp_path, "repositories: [o/meta]\nmetadata: false\n")
    out = tmp_path / "s"
    result = runner.invoke(
        app, ["index", "--config", str(cfg), "--out", str(out), "--token", "x"]
    )
    assert result.exit_code == 0, all_output(result)
    assert "have no .metadata asset" not in all_output(result)
    # advertising is disabled too, not just the warnings
    page = (out / "simple" / "hasmeta" / "index.html").read_text()
    assert "data-core-metadata" not in page
    assert (
        "core-metadata" not in (out / "simple" / "hasmeta" / "index.json").read_text()
    )


def test_cli_mirror_metadata_false_advertises_nothing(tmp_path, monkeypatch):
    from tests.test_index import METADATA_RELEASE

    monkeypatch.setattr(index, "fetch_releases", fetch_stub(METADATA_RELEASE))

    def fake_mirror(projects, out_dir, token, **kwargs):
        for files in projects.values():
            for entry in files:
                entry["sha256"] = "cafef00d"
                entry["url"] = f"../../files/x/{entry['filename']}"

    monkeypatch.setattr(index, "mirror_files", fake_mirror)
    cfg = config_file(
        tmp_path, "repositories: [o/meta]\nmirror: true\nmetadata: false\n"
    )
    out = tmp_path / "s"
    result = runner.invoke(
        app, ["index", "--config", str(cfg), "--out", str(out), "--token", "x"]
    )
    assert result.exit_code == 0, all_output(result)
    # a release-uploaded .metadata asset must NOT be advertised against the
    # rewritten local URL — no sibling exists there, resolvers would 404
    page = (out / "simple" / "hasmeta" / "index.html").read_text()
    assert "data-core-metadata" not in page
    assert (
        "core-metadata" not in (out / "simple" / "hasmeta" / "index.json").read_text()
    )


def test_cli_mirror_runs_extraction(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "fetch_releases", fetch_stub(FIXTURE_RELEASES))
    extract_calls = []

    def fake_mirror(projects, out_dir, token, **kwargs):
        for files in projects.values():
            for entry in files:
                entry["sha256"] = "cafef00d"
                entry["url"] = f"../../files/x/{entry['filename']}"

    monkeypatch.setattr(index, "mirror_files", fake_mirror)
    monkeypatch.setattr(
        index, "extract_metadata", lambda projects, out: extract_calls.append(out)
    )
    out = tmp_path / "site"
    result = runner.invoke(
        app,
        ["index", "bckohan/ghr-pypi", "--out", str(out), "--token", "t", "--mirror"],
    )
    assert result.exit_code == 0, all_output(result)
    assert extract_calls == [out]


def test_cli_mirror_metadata_false_skips_extraction(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "fetch_releases", fetch_stub(FIXTURE_RELEASES))
    monkeypatch.setattr(
        index,
        "mirror_files",
        lambda projects, out_dir, token, **kwargs: None,
    )
    called = []
    monkeypatch.setattr(
        index, "extract_metadata", lambda projects, out: called.append(1)
    )
    cfg = config_file(tmp_path, "repositories: [a/b]\nmirror: true\nmetadata: false\n")
    result = runner.invoke(
        app,
        ["index", "--config", str(cfg), "--out", str(tmp_path / "s"), "--token", "x"],
    )
    assert result.exit_code == 0, all_output(result)
    assert called == []


def test_cli_mirror_network_failure(tmp_path, monkeypatch):
    import urllib.error

    monkeypatch.setattr(index, "fetch_releases", fetch_stub(FIXTURE_RELEASES))

    def boom(projects, out_dir, token, **kwargs):
        raise urllib.error.URLError("nope")

    monkeypatch.setattr(index, "mirror_files", boom)
    result = runner.invoke(
        app,
        [
            "index",
            "bckohan/ghr-pypi",
            "--out",
            str(tmp_path),
            "--token",
            "t",
            "--mirror",
        ],
    )
    assert result.exit_code == 1
    assert "downloading a release asset failed" in all_output(result)


def test_cli_config_filters_flow_through(tmp_path, monkeypatch):
    # end-to-end guard on the cli -> collect_projects wiring: without
    # filters=cfg.filters the config's yanked/exclude blocks are inert
    monkeypatch.setattr(index, "fetch_releases", fetch_stub(FILTER_RELEASES))

    def never_hash(url):
        raise AssertionError("hash_url called; every fixture asset has a digest")

    monkeypatch.setattr(index, "hash_url", never_hash)
    cfg = config_file(
        tmp_path,
        """
repositories: [a/b]
yanked:
  Yankee:
    "1.1.0": "bad wheel"
exclude:
  yankee:
    - "1.0.0"
""",
    )
    out = tmp_path / "site"
    result = runner.invoke(
        app, ["index", "--config", str(cfg), "--out", str(out), "--token", "x"]
    )
    assert result.exit_code == 0, all_output(result)
    page = (out / "simple" / "yankee" / "index.html").read_text()
    assert 'data-yanked="bad wheel"' in page
    assert "yankee-1.0.0" not in page  # the excluded version never reached the index
    data = json.loads((out / "simple" / "yankee" / "index.json").read_text())
    assert data["versions"] == ["1.1.0"]
    assert data["files"][0]["yanked"] == "bad wheel"


def make_wheel(path, name="demo", version="1.0", payload="Name: demo\n"):
    wheel = path / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"{name}-{version}.dist-info/METADATA", payload)
    return wheel


def test_extract_meta_scans_a_directory(tmp_path):
    make_wheel(tmp_path, "one", payload="Name: one\n")
    make_wheel(tmp_path, "two", payload="Name: two\n")
    result = runner.invoke(app, ["extract-meta", str(tmp_path)])
    assert result.exit_code == 0, all_output(result)
    assert (
        tmp_path / "one-1.0-py3-none-any.whl.metadata"
    ).read_bytes() == b"Name: one\n"
    assert (
        tmp_path / "two-1.0-py3-none-any.whl.metadata"
    ).read_bytes() == b"Name: two\n"
    assert "2 wheel(s)" in result.output


def test_extract_meta_accepts_an_explicit_wheel(tmp_path):
    wheel = make_wheel(tmp_path)
    result = runner.invoke(app, ["extract-meta", str(wheel)])
    assert result.exit_code == 0, all_output(result)
    assert wheel.with_name(wheel.name + ".metadata").read_bytes() == b"Name: demo\n"


def test_extract_meta_does_not_recurse(tmp_path):
    make_wheel(tmp_path, "top")
    nested = tmp_path / "nested"
    nested.mkdir()
    buried = make_wheel(nested, "buried")
    result = runner.invoke(app, ["extract-meta", str(tmp_path)])
    assert result.exit_code == 0, all_output(result)
    assert not buried.with_name(buried.name + ".metadata").exists()


def test_extract_meta_overwrites(tmp_path):
    wheel = make_wheel(tmp_path)
    sidecar = wheel.with_name(wheel.name + ".metadata")
    sidecar.write_bytes(b"stale")
    result = runner.invoke(app, ["extract-meta", str(wheel)])
    assert result.exit_code == 0, all_output(result)
    assert sidecar.read_bytes() == b"Name: demo\n"


def test_extract_meta_rejects_a_missing_path(tmp_path):
    missing = tmp_path / "absent"
    result = runner.invoke(app, ["extract-meta", str(missing)])
    assert result.exit_code == 1
    assert f"{missing} does not exist" in all_output(result)


def test_extract_meta_rejects_a_non_wheel(tmp_path):
    other = tmp_path / "notes.txt"
    other.write_text("hi")
    result = runner.invoke(app, ["extract-meta", str(other)])
    assert result.exit_code == 1
    assert f"{other} is not a wheel" in all_output(result)


def test_extract_meta_rejects_an_empty_directory(tmp_path):
    result = runner.invoke(app, ["extract-meta", str(tmp_path)])
    assert result.exit_code == 1
    assert f"no wheels in {tmp_path}" in all_output(result)


def test_extract_meta_rejects_an_unreadable_wheel(tmp_path):
    wheel = tmp_path / "broken-1.0-py3-none-any.whl"
    wheel.write_bytes(b"not a zip")
    result = runner.invoke(app, ["extract-meta", str(wheel)])
    assert result.exit_code == 1
    assert "broken-1.0-py3-none-any.whl" in all_output(result)


def test_extract_meta_requires_a_path():
    result = runner.invoke(app, ["extract-meta"])
    # exit 2 alone would also be satisfied by the command not existing at all
    assert result.exit_code == 2
    assert "Missing argument" in all_output(result)


def test_extract_meta_deduplicates_paths(tmp_path):
    make_wheel(tmp_path, "one")
    result = runner.invoke(app, ["extract-meta", str(tmp_path), str(tmp_path)])
    assert result.exit_code == 0, all_output(result)
    assert "1 wheel(s)" in result.output


def test_extract_meta_writes_nothing_when_a_later_wheel_is_unreadable(tmp_path):
    good = make_wheel(tmp_path, "aaa")
    broken = tmp_path / "zzz-1.0-py3-none-any.whl"
    broken.write_bytes(b"not a zip")
    result = runner.invoke(app, ["extract-meta", str(tmp_path)])
    assert result.exit_code == 1
    # all-or-nothing: the readable wheel sorts first but must not be written
    assert not index.metadata_path(good).exists()


def test_extract_meta_reports_an_unwritable_sidecar(tmp_path, monkeypatch):
    wheel = make_wheel(tmp_path)

    def boom(self, data):
        raise OSError("Read-only file system")

    monkeypatch.setattr("pathlib.Path.write_bytes", boom)
    result = runner.invoke(app, ["extract-meta", str(wheel)])
    assert result.exit_code == 1
    assert f"cannot write {index.metadata_path(wheel)}" in all_output(result)


def test_bare_invocation_prints_help():
    result = runner.invoke(app, [])
    assert result.exit_code != 0
    assert "extract-meta" in all_output(result)


def fake_listing(monkeypatch, **owners):
    """Stub index.fetch_repositories, recording how many times each owner is listed."""
    calls = []

    def lister(owner, token, **kwargs):
        calls.append(owner)
        return list(owners[owner])

    monkeypatch.setattr(index, "fetch_repositories", lister)
    return calls


def test_expand_splices_matches_in_place(monkeypatch):
    # explicit entries on both sides: an expansion that appended instead of
    # splicing would still satisfy the leading entry, so the trailing one is
    # what actually pins the ordering guarantee
    fake_listing(monkeypatch, org=["org/beta", "org/alpha"])
    assert _expand_patterns(("first/explicit", "org/*", "z/explicit"), (), "tok") == (
        "first/explicit",
        "org/alpha",
        "org/beta",
        "z/explicit",
    )


def test_expand_lists_each_owner_once(monkeypatch):
    calls = fake_listing(monkeypatch, Org=["org/lib-a", "org/app-b"])
    # mixed-case owners are the same owner to GitHub; one listing must serve both
    assert _expand_patterns(("Org/lib-*", "org/app-*"), (), "tok") == (
        "org/lib-a",
        "org/app-b",
    )
    assert calls == ["Org"]


def test_expand_is_case_insensitive(monkeypatch):
    fake_listing(monkeypatch, org=["org/LibOne"])
    assert _expand_patterns(("org/lib*",), (), "tok") == ("org/LibOne",)


def test_expand_case_insensitivity_covers_the_pattern(monkeypatch):
    fake_listing(monkeypatch, org=["org/libone"])
    assert _expand_patterns(("org/Lib*",), (), "tok") == ("org/libone",)


def test_expand_applies_exclusions(monkeypatch):
    fake_listing(monkeypatch, org=["org/keep", "org/secret-x"])
    assert _expand_patterns(("org/*",), ("org/secret-*",), "tok") == ("org/keep",)


def test_expand_exclusions_are_case_insensitive(monkeypatch):
    fake_listing(monkeypatch, org=["org/keep", "org/Secret-X"])
    assert _expand_patterns(("org/*",), ("ORG/secret-*",), "tok") == ("org/keep",)


def test_expand_never_excludes_an_explicit_entry(monkeypatch):
    fake_listing(monkeypatch, org=["org/other"])
    assert _expand_patterns(("org/secret-x", "org/*"), ("org/secret-*",), "tok") == (
        "org/secret-x",
        "org/other",
    )


def test_expand_deduplicates_overlapping_patterns(monkeypatch):
    fake_listing(monkeypatch, org=["org/lib-a"])
    assert _expand_patterns(("org/*", "org/lib-*"), (), "tok") == ("org/lib-a",)


def test_expand_reports_only_newly_added_repositories(monkeypatch, capsys):
    # the second pattern re-matches what the first already claimed; reporting
    # its match count would promise more repositories than end up indexed
    fake_listing(monkeypatch, org=["org/lib-a", "org/lib-b", "org/app-c"])
    assert _expand_patterns(("org/*", "org/lib-*"), (), "tok") == (
        "org/app-c",
        "org/lib-a",
        "org/lib-b",
    )
    # capsys, not CliRunner: this asserts on stderr *specifically*, so dropping
    # err=True fails here. result.stderr cannot do that portably -- click < 8.2
    # merges the streams by default and raises on .stderr, which is why
    # all_output() exists, and the lowest-direct CI leg resolves such a click.
    captured = capsys.readouterr()
    assert "expanded 'org/*' to 3 repositories" in captured.err
    assert "expanded 'org/lib-*' to 0 repositories" in captured.err
    assert "expanded" not in captured.out


def test_expand_rejects_a_pattern_matching_nothing(monkeypatch):
    fake_listing(monkeypatch, org=["org/one"])
    with pytest.raises(ConfigError, match="no repositories matched 'org/zzz-\\*'"):
        _expand_patterns(("org/zzz-*",), (), "tok")


def test_expand_distinguishes_fully_excluded_from_unmatched(monkeypatch):
    # blaming the pattern here would send the user hunting for a typo in a
    # pattern that matched fine; the exclusion is what emptied it
    fake_listing(monkeypatch, org=["org/one"])
    with pytest.raises(ConfigError, match="every repository matching 'org/\\*'"):
        _expand_patterns(("org/*",), ("org/*",), "tok")


def test_expand_names_an_owner_that_does_not_exist(monkeypatch):
    def missing(owner, token, **kwargs):
        raise urllib.error.HTTPError(f"https://api/{owner}", 404, "nope", {}, None)

    monkeypatch.setattr(index, "fetch_repositories", missing)
    with pytest.raises(ConfigError, match="'typo' is not a visible organization"):
        _expand_patterns(("typo/*",), (), "tok")


def test_expand_reraises_a_non_404_listing_error(monkeypatch):
    def forbidden(owner, token, **kwargs):
        raise urllib.error.HTTPError(f"https://api/{owner}", 403, "no", {}, None)

    monkeypatch.setattr(index, "fetch_repositories", forbidden)
    with pytest.raises(urllib.error.HTTPError):
        _expand_patterns(("org/*",), (), "tok")


def test_expand_reports_each_expansion(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        index, "fetch_repositories", lambda owner, token, **kw: ["org/one"]
    )
    monkeypatch.setattr(index, "fetch_releases", fetch_stub(FIXTURE_RELEASES))
    monkeypatch.setattr(index, "hash_url", lambda url: "cafef00d")
    result = runner.invoke(app, ["index", "org/*", "--token", "x"])
    assert result.exit_code == 0, all_output(result)
    assert "expanded 'org/*' to 1 repositories" in all_output(result)


def releases_with(name, url_repo):
    """A one-release payload shipping a single wheel named ``name``."""
    return [
        {
            "tag_name": "v1.0.0",
            "assets": [
                {
                    "name": name,
                    "browser_download_url": (
                        f"https://github.com/{url_repo}/releases/download/v1.0.0/{name}"
                    ),
                },
            ],
        },
    ]


def test_cli_config_excludes_repositories_from_an_expansion(tmp_path, monkeypatch):
    # end-to-end: proves build_index actually hands exclude_repositories to
    # _expand_patterns. A unit test on _expand_patterns cannot see that wiring.
    monkeypatch.setattr(
        index,
        "fetch_repositories",
        lambda owner, token, **kw: ["org/public", "org/secret-internal"],
    )
    by_repo = {
        "org/public": releases_with("keeper-1.0.0-py3-none-any.whl", "org/public"),
        "org/secret-internal": releases_with(
            "hushhush-1.0.0-py3-none-any.whl", "org/secret-internal"
        ),
    }
    monkeypatch.setattr(
        index, "fetch_releases", lambda repo, token: [dict(r) for r in by_repo[repo]]
    )
    monkeypatch.setattr(index, "hash_url", lambda url: "cafef00d")
    cfg = config_file(
        tmp_path,
        "repositories: [org/*]\nexclude_repositories: [org/secret-*]\n",
    )
    out = tmp_path / "site"
    result = runner.invoke(
        app, ["index", "--config", str(cfg), "--out", str(out), "--token", "x"]
    )
    assert result.exit_code == 0, all_output(result)
    assert (out / "simple" / "keeper").is_dir()
    assert not (out / "simple" / "hushhush").exists()
    assert "wrote index for 1 project(s)" in result.output


def test_cli_expansion_order_decides_a_duplicate_filename(tmp_path, monkeypatch):
    # the whole reason expansions are sorted and spliced in place: duplicate
    # filenames across repositories resolve first-occurrence-wins, so the
    # alphabetically-first repository's copy is the one that gets indexed
    duplicate = "twinned-1.0.0-py3-none-any.whl"
    monkeypatch.setattr(
        index,
        "fetch_repositories",
        lambda owner, token, **kw: ["org/zeta", "org/alpha"],
    )
    monkeypatch.setattr(
        index,
        "fetch_releases",
        lambda repo, token: [dict(r) for r in releases_with(duplicate, repo)],
    )
    monkeypatch.setattr(index, "hash_url", lambda url: "cafef00d")
    out = tmp_path / "site"
    result = runner.invoke(app, ["index", "org/*", "--out", str(out), "--token", "x"])
    assert result.exit_code == 0, all_output(result)
    page = (out / "simple" / "twinned" / "index.html").read_text()
    assert "org/alpha/releases" in page
    assert "org/zeta/releases" not in page
    assert f"duplicate asset {duplicate} ignored" in all_output(result)


def test_resolve_rejects_a_pattern_in_the_environment():
    with pytest.raises(ConfigError, match="may not be a pattern"):
        resolve(env_repo="org/*")


def test_resolve_accepts_a_pattern_argument():
    assert resolve(["org/*"]).repositories == ("org/*",)


def test_cli_reports_a_pattern_matching_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        index, "fetch_repositories", lambda owner, token, **kw: ["org/one"]
    )
    result = runner.invoke(
        app, ["index", "org/zzz-*", "--out", str(tmp_path), "--token", "x"]
    )
    assert result.exit_code == 1
    assert "no repositories matched 'org/zzz-*'" in all_output(result)


def test_cli_reports_a_listing_failure(tmp_path, monkeypatch):
    import urllib.error

    def boom(owner, token, **kwargs):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(index, "fetch_repositories", boom)
    result = runner.invoke(
        app, ["index", "org/*", "--out", str(tmp_path), "--token", "x"]
    )
    assert result.exit_code == 1
    assert "listing repositories failed" in all_output(result)


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
            "index",
            "a/b",
            "--out",
            str(tmp_path / "site"),
            "--token",
            "x",
            "--target",
            "cloudflare",
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
            "index",
            "a/b",
            "--out",
            str(tmp_path / "site"),
            "--token",
            "x",
            "--target",
            "nginx",
            "--target-out",
            str(ops),
        ],
    )
    assert result.exit_code == 0, all_output(result)
    assert (ops / "ghr-pypi.conf").exists()
    assert not (tmp_path / "site" / "ghr-pypi.conf").exists()


def test_cli_default_target_writes_no_artifacts(tmp_path, monkeypatch):
    # assert the absence of *any* artifact in both directories, not one
    # filename in one of them: a default of "nginx" writes nothing named
    # _headers and would sail past a narrower check while dropping a server
    # snippet into the working directory
    monkeypatch.setattr(index, "fetch_releases", fetch_stub(FIXTURE_RELEASES))
    monkeypatch.setattr(index, "hash_url", lambda url: "cafef00d")
    out = tmp_path / "site"
    ops = tmp_path / "ops"
    result = runner.invoke(
        app,
        ["index", "a/b", "--out", str(out), "--token", "x", "--target-out", str(ops)],
    )
    assert result.exit_code == 0, all_output(result)
    # write_site owns everything under out; the target must add nothing beside it
    assert sorted(p.name for p in out.iterdir()) == ["index.html", "simple"]
    assert list(ops.iterdir()) == []
    assert "for the" not in all_output(result)


def test_cli_target_from_a_config_file(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "fetch_releases", fetch_stub(FIXTURE_RELEASES))
    monkeypatch.setattr(index, "hash_url", lambda url: "cafef00d")
    cfg = config_file(tmp_path, "repositories: [a/b]\ntarget: cloudflare\n")
    out = tmp_path / "site"
    result = runner.invoke(
        app, ["index", "--config", str(cfg), "--out", str(out), "--token", "x"]
    )
    assert result.exit_code == 0, all_output(result)
    assert (out / "_headers").exists()


def test_cli_target_out_is_allowed_with_a_config(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "fetch_releases", fetch_stub(FIXTURE_RELEASES))
    monkeypatch.setattr(index, "hash_url", lambda url: "cafef00d")
    cfg = config_file(tmp_path, "repositories: [a/b]\ntarget: nginx\n")
    ops = tmp_path / "ops"
    result = runner.invoke(
        app,
        [
            "index",
            "--config",
            str(cfg),
            "--out",
            str(tmp_path / "site"),
            "--token",
            "x",
            "--target-out",
            str(ops),
        ],
    )
    assert result.exit_code == 0, all_output(result)
    assert (ops / "ghr-pypi.conf").exists()


def test_cli_reports_each_artifact_it_wrote(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "fetch_releases", fetch_stub(FIXTURE_RELEASES))
    monkeypatch.setattr(index, "hash_url", lambda url: "cafef00d")
    ops = tmp_path / "ops"
    result = runner.invoke(
        app,
        [
            "index",
            "a/b",
            "--out",
            str(tmp_path / "site"),
            "--token",
            "x",
            "--target",
            "nginx",
            "--target-out",
            str(ops),
        ],
    )
    assert result.exit_code == 0, all_output(result)
    assert f"wrote {ops / 'ghr-pypi.conf'} for the nginx target" in all_output(result)


def test_cli_unwritable_target_out_fails_before_the_build(tmp_path, monkeypatch):
    # the same failure shape the early get_target resolve prevents: knowable
    # from the arguments, so it must not surface after the whole site is built
    def explode(repo, token):
        raise AssertionError("the build must not start with an unusable --target-out")

    monkeypatch.setattr(index, "fetch_releases", explode)
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    result = runner.invoke(
        app,
        [
            "index",
            "a/b",
            "--out",
            str(tmp_path / "site"),
            "--token",
            "x",
            "--target",
            "nginx",
            "--target-out",
            str(blocker / "ops"),
        ],
    )
    assert result.exit_code == 1
    assert "cannot create --target-out" in all_output(result)
    assert not (tmp_path / "site").exists()


def _target_run(
    tmp_path, monkeypatch, target, *, config=None, releases=None, fetch=None
):
    """Run a build whose target is ``target``, stubbing out the network.

    Always passes --target-out. Its default is the working directory, so a
    test double that writes an artifact would otherwise drop it in the repo
    root for the driver to commit by accident.

    ``config`` routes the run through a config file instead of the command
    line — the only way to ask for an asset mode that has no flag. The
    repository and the target then come from that file, because the CLI
    refuses both beside --config.
    """
    monkeypatch.setattr(
        index, "fetch_releases", fetch or fetch_stub(releases or FIXTURE_RELEASES)
    )
    monkeypatch.setattr(index, "hash_url", lambda url: "cafef00d")
    monkeypatch.setattr("ghr_pypi.cli.get_target", lambda name: target)
    source = ["--config", str(config)] if config else ["a/b", "--target", target.name]
    return runner.invoke(
        app,
        [
            "index",
            *source,
            "--out",
            str(tmp_path / "site"),
            "--token",
            "x",
            "--target-out",
            str(tmp_path / "ops"),
        ],
    )


def test_cli_reports_a_failing_target(tmp_path, monkeypatch):
    class Boom:
        name = "cloudflare"

        def emit(self, site):
            raise OSError("disk full")

    result = _target_run(tmp_path, monkeypatch, Boom())
    assert result.exit_code == 1
    assert "cloudflare target failed: disk full" in all_output(result)


def test_cli_catches_a_generator_targets_deferred_failure(tmp_path, monkeypatch):
    # a generator defers its writes to whenever the caller iterates. If the
    # CLI iterated lazily while reporting, the write — and its OSError — would
    # land outside the error handling, after the success line was printed.
    class Lazy:
        name = "lazy"

        def emit(self, site):
            def generate():
                yield site.target_dir / "first.conf"
                raise OSError("disk full")

            return generate()

    result = _target_run(tmp_path, monkeypatch, Lazy())
    assert result.exit_code == 1
    assert "lazy target failed: disk full" in all_output(result)
    # the traceback-and-claim-success failure mode this guards
    assert "Traceback" not in all_output(result)
    assert "wrote index for" not in all_output(result)


def test_cli_rejects_a_target_returning_no_paths(tmp_path, monkeypatch):
    # the shape a plugin author gets by forgetting the return statement
    class Forgetful:
        name = "forgetful"

        def emit(self, site):
            (site.target_dir / "artifact.conf").write_text("hi")

    result = _target_run(tmp_path, monkeypatch, Forgetful())
    assert result.exit_code == 1
    assert (
        "forgetful target returned NoneType, expected a sequence of paths"
        in all_output(result)
    )
    assert "Traceback" not in all_output(result)


def _asset(name, asset_id):
    """A release asset carrying the API url redirect mode needs."""
    return {
        "name": name,
        "browser_download_url": (
            f"https://github.com/a/b/releases/download/demo-lib-v1.0.0/{name}"
        ),
        **(
            {"url": f"https://api.github.com/repos/a/b/releases/assets/{asset_id}"}
            if asset_id is not None
            else {}
        ),
    }


WHEEL = "ghr_pypi_demo_lib-1.0.0-py3-none-any.whl"


def redirect_releases(*assets):
    return [{"tag_name": "demo-lib-v1.0.0", "assets": list(assets)}]


SIDECAR_RELEASES = redirect_releases(
    _asset(WHEEL, 11),
    _asset(f"{WHEEL}.metadata", 12),
    _asset("ghr_pypi_demo_lib-1.0.0.tar.gz", 13),
)
"""Every wheel has a sidecar, so nothing needs downloading to extract."""


def redirect_config(tmp_path, extra=""):
    return config_file(
        tmp_path,
        "repositories: [a/b]\nassets: redirect\ntarget: cloudflare\n" + extra,
    )


def test_cli_refuses_redirect_on_a_target_that_cannot_serve_it(tmp_path, monkeypatch):
    # knowable from the arguments alone, so it must not surface after every
    # wheel has been downloaded and the whole site written
    class Plain:
        name = "nginx"

        def emit(self, site):
            raise AssertionError("the build must not start")

    def explode(repo, token):
        raise AssertionError("the build must not start")

    config = config_file(
        tmp_path, "repositories: [a/b]\nassets: redirect\ntarget: nginx\n"
    )
    result = _target_run(tmp_path, monkeypatch, Plain(), config=config, fetch=explode)
    assert result.exit_code == 1
    output = all_output(result)
    assert "target 'nginx' cannot serve 'assets: redirect'" in output
    assert "targets that can: cloudflare" in output
    assert not (tmp_path / "site").exists()
    assert "Traceback" not in output


def test_cli_redirect_writes_the_manifest_and_the_worker(tmp_path, monkeypatch):
    result = _target_run(
        tmp_path,
        monkeypatch,
        get_target("cloudflare"),
        config=redirect_config(tmp_path),
        releases=SIDECAR_RELEASES,
    )
    assert result.exit_code == 0, all_output(result)
    site = tmp_path / "site"
    manifest = json.loads((site / "_assets" / "manifest.json").read_text())
    assert manifest["assets"]["11"] == {
        "repo": "a/b",
        "filename": WHEEL,
        "metadata_id": "12",
    }
    assert (site / "_worker.js").exists()
    # the index must actually point at the redirector, not at GitHub
    page = (site / "simple" / "ghr-pypi-demo-lib" / "index.html").read_text()
    assert f"../../_assets/11/{WHEEL}" in page
    assert "https://github.com/a/b/releases/download" not in page


def test_cli_redirect_extracts_metadata_for_sidecarless_wheels(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        index,
        "extract_missing_metadata",
        lambda projects, out, token: calls.append((sorted(projects), out, token)),
    )
    result = _target_run(
        tmp_path,
        monkeypatch,
        get_target("cloudflare"),
        config=redirect_config(tmp_path),
        releases=redirect_releases(_asset(WHEEL, 11)),
    )
    assert result.exit_code == 0, all_output(result)
    assert calls == [(["ghr-pypi-demo-lib"], tmp_path / "site", "x")]


def test_cli_redirect_warn_warns_instead_of_downloading(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        index, "extract_missing_metadata", lambda *args: calls.append(args)
    )
    result = _target_run(
        tmp_path,
        monkeypatch,
        get_target("cloudflare"),
        config=redirect_config(tmp_path, "missing_metadata: warn\n"),
        releases=redirect_releases(_asset(WHEEL, 11)),
    )
    assert result.exit_code == 0, all_output(result)
    assert calls == []
    assert "warning: a/b: 1 of 1 wheels have no .metadata asset" in all_output(result)
    assert (tmp_path / "site" / "_assets" / "manifest.json").exists()


def test_cli_redirect_rejects_a_bad_entry_before_downloading(tmp_path, monkeypatch):
    # redirect_urls runs before extract_missing_metadata precisely so an entry
    # GitHub gave no API url is refused before a single wheel is fetched. The
    # sidecar-less wheel below is what a swapped order would download first.
    calls = []
    monkeypatch.setattr(
        index, "extract_missing_metadata", lambda *args: calls.append(args)
    )
    result = _target_run(
        tmp_path,
        monkeypatch,
        get_target("cloudflare"),
        config=redirect_config(tmp_path),
        releases=redirect_releases(
            _asset(WHEEL, 11),
            _asset("ghr_pypi_demo_lib-1.0.0.tar.gz", None),
        ),
    )
    assert result.exit_code == 1
    output = all_output(result)
    assert "redirect mode cannot serve it" in output
    assert calls == [], "a malformed entry must fail before anything is downloaded"
    assert "Traceback" not in output


def test_cli_redirect_reports_a_download_failure(tmp_path, monkeypatch):
    # defence in depth: extract_missing_metadata warns and continues on the
    # OSError subclasses a download actually raises, so nothing in the happy
    # path reaches this handler today. It exists so that if that ever changes
    # the operator gets a message instead of a traceback.
    def boom(projects, out, token):
        raise urllib.error.URLError("connection reset")

    monkeypatch.setattr(index, "extract_missing_metadata", boom)
    result = _target_run(
        tmp_path,
        monkeypatch,
        get_target("cloudflare"),
        config=redirect_config(tmp_path),
        releases=redirect_releases(_asset(WHEEL, 11)),
    )
    assert result.exit_code == 1
    assert "downloading a release asset failed" in all_output(result)
    assert "Traceback" not in all_output(result)
