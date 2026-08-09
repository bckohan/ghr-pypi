import hashlib
import io
import json
import tempfile
import urllib.error
import zipfile

import pytest

from ghr_pypi import index
from ghr_pypi.config import Filters

FIXTURE_RELEASES = [
    {
        "tag_name": "demo-lib-v1.0.0",
        "assets": [
            {
                "name": "ghr_pypi_demo_lib-1.0.0-py3-none-any.whl",
                "browser_download_url": "https://github.com/bckohan/ghr-pypi/releases/download/demo-lib-v1.0.0/ghr_pypi_demo_lib-1.0.0-py3-none-any.whl",
            },
            {
                "name": "ghr_pypi_demo_lib-1.0.0.tar.gz",
                "browser_download_url": "https://github.com/bckohan/ghr-pypi/releases/download/demo-lib-v1.0.0/ghr_pypi_demo_lib-1.0.0.tar.gz",
            },
            {
                "name": "release-notes.txt",
                "browser_download_url": "https://github.com/bckohan/ghr-pypi/releases/download/demo-lib-v1.0.0/release-notes.txt",
            },
        ],
    },
    {
        "tag_name": "demo-app-v1.0.0",
        "assets": [
            {
                "name": "ghr_pypi_demo_app-1.0.0-py3-none-any.whl",
                "browser_download_url": "https://github.com/bckohan/ghr-pypi/releases/download/demo-app-v1.0.0/ghr_pypi_demo_app-1.0.0-py3-none-any.whl",
            },
        ],
    },
    {
        "tag_name": "demo-lib-v2.0.0",
        "draft": True,
        "assets": [
            {
                "name": "ghr_pypi_demo_lib-2.0.0-py3-none-any.whl",
                "browser_download_url": "https://github.com/bckohan/ghr-pypi/releases/download/demo-lib-v2.0.0/ghr_pypi_demo_lib-2.0.0-py3-none-any.whl",
            },
        ],
    },
]


def fake_hash(url):
    return "cafef00d"


def paged_opener(*pages):
    """Fake opener serving JSON pages in order.

    Records each call's URL, full request (for headers), and timeout so
    tests can assert on what was actually sent, not just how many calls
    happened.
    """
    urls = []
    requests = []
    timeouts = []

    def opener(request, timeout=None):
        page = len(urls)
        assert page < len(pages), (
            f"paged_opener requested page {page + 1} but only "
            f"{len(pages)} page(s) were supplied"
        )
        urls.append(request.full_url)
        requests.append(request)
        timeouts.append(timeout)
        return io.BytesIO(json.dumps(pages[page]).encode())

    opener.urls = urls
    opener.requests = requests
    opener.timeouts = timeouts
    return opener


def test_paginate_single_short_page():
    opener = paged_opener([{"n": 1}, {"n": 2}])
    assert index._paginate("https://api.example/x", "tok", opener=opener) == [
        {"n": 1},
        {"n": 2},
    ]
    assert opener.urls == ["https://api.example/x?per_page=100&page=1"]
    assert opener.requests[0].get_header("Authorization") == "Bearer tok"
    assert opener.requests[0].get_header("Accept") == "application/vnd.github+json"
    assert opener.timeouts == [30]


def test_paginate_full_page_then_short():
    first = [{"n": i} for i in range(100)]
    opener = paged_opener(first, [{"n": 100}])
    assert index._paginate("https://api.example/x", "tok", opener=opener) == first + [
        {"n": 100}
    ]
    assert opener.urls[-1].endswith("page=2")


def test_paginate_full_page_then_empty():
    first = [{"n": i} for i in range(100)]
    opener = paged_opener(first, [])
    assert index._paginate("https://api.example/x", "tok", opener=opener) == first
    assert len(opener.urls) == 2


def test_paginate_refuses_to_loop_forever():
    full = [{"n": i} for i in range(100)]
    opener = paged_opener(*([full] * 200))
    with pytest.raises(index.PaginationError, match="more than 10000 items"):
        index._paginate("https://api.example/x", "tok", opener=opener)
    assert len(opener.urls) == 100  # pins the cap's value, not just its existence


def test_paginate_rejects_non_https():
    with pytest.raises(ValueError, match="non-https"):
        index._paginate("http://api.example/x", "tok", opener=paged_opener())


def test_fetch_releases_reads_every_page():
    first = [{"tag_name": f"v{i}", "assets": []} for i in range(100)]
    opener = paged_opener(first, [{"tag_name": "v100", "assets": []}])
    releases = index.fetch_releases("a/b", "tok", opener=opener)
    assert len(releases) == 101
    assert releases[-1]["tag_name"] == "v100"
    assert opener.urls[0].startswith(f"{index.API_ROOT}/repos/a/b/releases?")


def test_fetch_repositories_lists_an_org():
    opener = paged_opener([{"full_name": "org/one"}, {"full_name": "org/two"}])
    assert index.fetch_repositories("org", "tok", opener=opener) == [
        "org/one",
        "org/two",
    ]
    assert "/orgs/org/repos" in opener.urls[0]


def test_fetch_repositories_falls_back_to_a_user():
    calls = []

    def opener(request, timeout=None):
        calls.append(request.full_url)
        if "/orgs/" in request.full_url:
            raise urllib.error.HTTPError(request.full_url, 404, "nope", {}, None)
        return io.BytesIO(json.dumps([{"full_name": "someone/pkg"}]).encode())

    assert index.fetch_repositories("someone", "tok", opener=opener) == ["someone/pkg"]
    assert "/users/someone/repos" in calls[-1]


def test_fetch_repositories_reraises_other_http_errors():
    def opener(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 403, "forbidden", {}, None)

    with pytest.raises(urllib.error.HTTPError):
        index.fetch_repositories("org", "tok", opener=opener)


def test_normalize():
    assert index.normalize("GHR_PyPI--Demo") == "ghr-pypi-demo"


def test_project_name_from_filename():
    assert (
        index.project_name_from_filename("ghr_pypi_demo_lib-1.0.0-py3-none-any.whl")
        == "ghr_pypi_demo_lib"
    )
    assert (
        index.project_name_from_filename("ghr_pypi_demo_lib-1.0.0.tar.gz")
        == "ghr_pypi_demo_lib"
    )
    assert index.project_name_from_filename("release-notes.txt") is None


def test_collect_projects():
    projects = index.collect_projects(FIXTURE_RELEASES, hash_url=fake_hash)
    assert sorted(projects) == [
        "ghr-pypi-demo-app",
        "ghr-pypi-demo-lib",
    ]
    lib_files = projects["ghr-pypi-demo-lib"]
    assert [f["filename"] for f in lib_files] == [
        "ghr_pypi_demo_lib-1.0.0-py3-none-any.whl",
        "ghr_pypi_demo_lib-1.0.0.tar.gz",
    ]
    assert all(f["sha256"] == "cafef00d" for f in lib_files)


def test_collect_projects_dedupes_duplicate_filenames(capsys):
    duplicate = {
        "tag_name": "mirror-v1.0.0",
        "assets": [
            {
                "name": "ghr_pypi_demo_lib-1.0.0-py3-none-any.whl",
                "browser_download_url": "https://github.com/other/mirror/releases/download/v1/ghr_pypi_demo_lib-1.0.0-py3-none-any.whl",
            },
        ],
    }
    hashed = []

    def counting_hash(url):
        hashed.append(url)
        return "cafef00d"

    projects = index.collect_projects(
        FIXTURE_RELEASES + [duplicate], hash_url=counting_hash
    )
    lib_files = projects["ghr-pypi-demo-lib"]
    whl = [f for f in lib_files if f["filename"].endswith(".whl")]
    assert len(whl) == 1
    assert "bckohan/ghr-pypi" in whl[0]["url"]  # first occurrence won
    assert not any("other/mirror" in url for url in hashed)  # duplicate never hashed
    assert (
        "duplicate asset ghr_pypi_demo_lib-1.0.0-py3-none-any.whl"
        in capsys.readouterr().err
    )


def test_collect_projects_skips_unsafe_asset_names(capsys):
    release = {
        "tag_name": "v6.6.6",
        "assets": [
            {
                "name": "../../evil-1.0.0-py3-none-any.whl",
                "browser_download_url": "https://github.com/o/r/releases/download/v6.6.6/a",
            },
            {
                "name": "/abs/evil-1.0.0-py3-none-any.whl",
                "browser_download_url": "https://github.com/o/r/releases/download/v6.6.6/b",
            },
        ],
    }
    projects = index.collect_projects([release], hash_url=never_hash)
    assert projects == {}
    err = capsys.readouterr().err
    assert "unsafe name '../../evil-1.0.0-py3-none-any.whl'" in err
    assert "unsafe name '/abs/evil-1.0.0-py3-none-any.whl'" in err


def test_write_site(tmp_path):
    projects = index.collect_projects(FIXTURE_RELEASES, hash_url=fake_hash)
    index.write_site(
        projects,
        tmp_path,
        title="bckohan/ghr-pypi package index",
        index_url="https://bckohan.github.io/ghr-pypi/simple/",
    )

    landing = (tmp_path / "index.html").read_text()
    assert "https://bckohan.github.io/ghr-pypi/simple/" in landing
    assert "bckohan/ghr-pypi package index" in landing

    root = (tmp_path / "simple" / "index.html").read_text()
    assert '<a href="ghr-pypi-demo-lib/">' in root
    assert '<a href="ghr-pypi-demo-app/">' in root

    lib_page = (tmp_path / "simple" / "ghr-pypi-demo-lib" / "index.html").read_text()
    assert "#sha256=cafef00d" in lib_page
    assert "ghr_pypi_demo_lib-1.0.0-py3-none-any.whl</a>" in lib_page
    assert '<meta name="pypi:repository-version" content="1.0"' in lib_page


def test_write_site_without_index_url(tmp_path):
    projects = index.collect_projects(FIXTURE_RELEASES, hash_url=fake_hash)
    index.write_site(projects, tmp_path, title="An index", index_url=None)
    landing = (tmp_path / "index.html").read_text()
    assert "--extra-index-url" not in landing
    assert '<a href="simple/">' in landing


def test_write_site_template_override(tmp_path):
    overrides = tmp_path / "tpl"
    overrides.mkdir()
    (overrides / "landing.html").write_text("<html>custom landing</html>\n")
    projects = index.collect_projects(FIXTURE_RELEASES, hash_url=fake_hash)
    index.write_site(
        projects,
        tmp_path / "site",
        title="T",
        index_url=None,
        templates_dir=overrides,
    )
    assert (
        tmp_path / "site" / "index.html"
    ).read_text() == "<html>custom landing</html>\n"
    # other templates still fall back to the built-ins
    root = (tmp_path / "site" / "simple" / "index.html").read_text()
    assert '<meta name="pypi:repository-version" content="1.0"' in root


def test_write_site_block_extension(tmp_path):
    overrides = tmp_path / "tpl"
    overrides.mkdir()
    (overrides / "landing.html").write_text(
        '{% extends "builtin/landing.html" %}'
        "{% block footer %}<footer>custom footer</footer>{% endblock %}"
    )
    projects = index.collect_projects(FIXTURE_RELEASES, hash_url=fake_hash)
    index.write_site(
        projects,
        tmp_path / "site",
        title="Extended",
        index_url=None,
        templates_dir=overrides,
    )
    landing = (tmp_path / "site" / "index.html").read_text()
    assert "<footer>custom footer</footer>" in landing
    assert "Available packages:" in landing  # built-in content block retained


DIGEST_RELEASE = [
    {
        "tag_name": "v9.0.0",
        "assets": [
            {
                "name": "digested-9.0.0-py3-none-any.whl",
                "browser_download_url": "https://github.com/o/r/releases/download/v9.0.0/digested-9.0.0-py3-none-any.whl",
                "digest": "sha256:feedbeef",
            },
            {
                "name": "legacy-9.0.0-py3-none-any.whl",
                "browser_download_url": "https://github.com/o/r/releases/download/v9.0.0/legacy-9.0.0-py3-none-any.whl",
            },
        ],
    },
]


def never_hash(url):
    raise AssertionError(f"hash_url called for {url}")


def test_digest_used_without_download():
    projects = index.collect_projects(
        [DIGEST_RELEASE[0] | {"assets": DIGEST_RELEASE[0]["assets"][:1]}],
        hash_url=never_hash,
    )
    assert projects["digested"][0]["sha256"] == "feedbeef"


def test_non_sha256_digest_falls_back_to_download():
    release = {
        "tag_name": "v9.0.1",
        "assets": [
            {
                "name": "oddhash-9.0.1-py3-none-any.whl",
                "browser_download_url": "https://github.com/o/r/releases/download/v9.0.1/oddhash-9.0.1-py3-none-any.whl",
                "digest": "blake2:abc123",
            },
        ],
    }
    projects = index.collect_projects([release], hash_url=fake_hash)
    assert projects["oddhash"][0]["sha256"] == "cafef00d"


def test_missing_digest_download_default():
    projects = index.collect_projects(DIGEST_RELEASE, hash_url=fake_hash)
    assert projects["legacy"][0]["sha256"] == "cafef00d"
    assert projects["digested"][0]["sha256"] == "feedbeef"


def test_missing_digest_no_fragment(tmp_path):
    projects = index.collect_projects(
        DIGEST_RELEASE, hash_url=never_hash, missing_digest="no-fragment"
    )
    assert projects["legacy"][0]["sha256"] is None
    index.write_site(projects, tmp_path, title="T", index_url=None)
    page = (tmp_path / "simple" / "legacy" / "index.html").read_text()
    assert "#sha256=" not in page
    assert "legacy-9.0.0-py3-none-any.whl</a>" in page
    digested_page = (tmp_path / "simple" / "digested" / "index.html").read_text()
    assert "#sha256=feedbeef" in digested_page


def test_missing_digest_omit(capsys):
    projects = index.collect_projects(
        DIGEST_RELEASE, hash_url=never_hash, missing_digest="omit"
    )
    assert "legacy" not in projects
    assert "digested" in projects
    err = capsys.readouterr().err
    assert "legacy-9.0.0-py3-none-any.whl has no digest, omitted" in err


def test_null_digest_treated_as_absent():
    # GitHub's API returns "digest": null for legacy assets — pin that wire
    # shape: an explicit None behaves exactly like a missing key.
    release = {
        "tag_name": "v9.0.3",
        "assets": [
            {
                "name": "nulldigest-9.0.3-py3-none-any.whl",
                "browser_download_url": "https://github.com/o/r/releases/download/v9.0.3/nulldigest-9.0.3-py3-none-any.whl",
                "digest": None,
            },
        ],
    }
    projects = index.collect_projects([release], hash_url=fake_hash)
    assert projects["nulldigest"][0]["sha256"] == "cafef00d"


def test_empty_sha256_digest_treated_as_absent(capsys):
    release = {
        "tag_name": "v9.0.2",
        "assets": [
            {
                "name": "emptyhash-9.0.2-py3-none-any.whl",
                "browser_download_url": "https://github.com/o/r/releases/download/v9.0.2/emptyhash-9.0.2-py3-none-any.whl",
                "digest": "sha256:",
            },
        ],
    }
    projects = index.collect_projects(
        [release], hash_url=never_hash, missing_digest="omit"
    )
    assert "emptyhash" not in projects
    err = capsys.readouterr().err
    assert "emptyhash-9.0.2-py3-none-any.whl has no digest, omitted" in err


def test_collect_projects_captures_api_url():
    release = {
        "tag_name": "v5.0.0",
        "assets": [
            {
                "name": "apiurl-5.0.0-py3-none-any.whl",
                "browser_download_url": "https://github.com/o/r/releases/download/v5.0.0/apiurl-5.0.0-py3-none-any.whl",
                "url": "https://api.github.com/repos/o/r/releases/assets/123",
                "digest": "sha256:beef",
            },
        ],
    }
    entry = index.collect_projects([release], hash_url=never_hash)["apiurl"][0]
    assert entry["api_url"] == "https://api.github.com/repos/o/r/releases/assets/123"


def test_collect_projects_api_url_defaults_to_empty():
    release = {
        "tag_name": "v5.0.1",
        "assets": [
            {
                "name": "nourl-5.0.1-py3-none-any.whl",
                "browser_download_url": "https://github.com/o/r/releases/download/v5.0.1/nourl-5.0.1-py3-none-any.whl",
                "digest": "sha256:beef",
            },
        ],
    }
    entry = index.collect_projects([release], hash_url=never_hash)["nourl"][0]
    assert entry["api_url"] == ""


def test_collect_projects_defer_hash():
    projects = index.collect_projects(
        DIGEST_RELEASE, hash_url=never_hash, defer_hash=True
    )
    assert projects["digested"][0]["sha256"] == "feedbeef"
    assert projects["legacy"][0]["sha256"] is None


def test_collect_projects_defer_hash_ignores_policy():
    projects = index.collect_projects(
        DIGEST_RELEASE, hash_url=never_hash, defer_hash=True, missing_digest="omit"
    )
    assert "legacy" in projects  # omit does not apply under defer_hash


def make_opener(payload=b"wheel-bytes", requests_log=None):
    def opener(request, timeout=None):
        if requests_log is not None:
            requests_log.append(request)
        return io.BytesIO(payload)

    return opener


MIRROR_RELEASE = [
    {
        "tag_name": "v7.0.0",
        "assets": [
            {
                "name": "mirrored-7.0.0-py3-none-any.whl",
                "browser_download_url": "https://github.com/o/r/releases/download/v7.0.0/mirrored-7.0.0-py3-none-any.whl",
                "url": "https://api.github.com/repos/o/r/releases/assets/7",
            },
        ],
    },
]


def mirror_projects():
    return index.collect_projects(MIRROR_RELEASE, hash_url=never_hash, defer_hash=True)


def test_mirror_files_downloads_and_relinks(tmp_path):
    projects = mirror_projects()
    log = []
    index.mirror_files(projects, tmp_path, "tok", opener=make_opener(requests_log=log))
    entry = projects["mirrored"][0]
    dest = tmp_path / "files" / "mirrored" / "mirrored-7.0.0-py3-none-any.whl"
    assert dest.read_bytes() == b"wheel-bytes"
    assert entry["sha256"] == hashlib.sha256(b"wheel-bytes").hexdigest()
    assert entry["url"] == "../../files/mirrored/mirrored-7.0.0-py3-none-any.whl"
    assert len(log) == 1
    assert log[0].get_full_url() == "https://api.github.com/repos/o/r/releases/assets/7"
    assert log[0].get_header("Accept") == "application/octet-stream"
    assert log[0].get_header("Authorization") == "Bearer tok"


def test_mirror_files_reuses_matching_existing(tmp_path):
    projects = mirror_projects()
    dest = tmp_path / "files" / "mirrored" / "mirrored-7.0.0-py3-none-any.whl"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"already-here")
    projects["mirrored"][0]["sha256"] = hashlib.sha256(b"already-here").hexdigest()
    log = []
    index.mirror_files(projects, tmp_path, "tok", opener=make_opener(requests_log=log))
    assert log == []  # no download
    assert dest.read_bytes() == b"already-here"


def test_mirror_files_adopts_existing_hash(tmp_path):
    projects = mirror_projects()  # sha256 is None (no digest)
    dest = tmp_path / "files" / "mirrored" / "mirrored-7.0.0-py3-none-any.whl"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"already-here")
    log = []
    index.mirror_files(projects, tmp_path, "tok", opener=make_opener(requests_log=log))
    assert log == []
    assert (
        projects["mirrored"][0]["sha256"] == hashlib.sha256(b"already-here").hexdigest()
    )


def test_mirror_files_redownloads_corrupted(tmp_path):
    projects = mirror_projects()
    dest = tmp_path / "files" / "mirrored" / "mirrored-7.0.0-py3-none-any.whl"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"corrupted")
    projects["mirrored"][0]["sha256"] = hashlib.sha256(b"wheel-bytes").hexdigest()
    log = []
    index.mirror_files(projects, tmp_path, "tok", opener=make_opener(requests_log=log))
    assert len(log) == 1
    assert dest.read_bytes() == b"wheel-bytes"


def test_mirror_files_digest_mismatch(tmp_path):
    projects = mirror_projects()
    projects["mirrored"][0]["sha256"] = "deadbeef"  # advertised digest, wrong
    with pytest.raises(index.MirrorError, match="mirrored-7.0.0-py3-none-any.whl"):
        index.mirror_files(projects, tmp_path, "tok", opener=make_opener())
    assert not (
        tmp_path / "files" / "mirrored" / "mirrored-7.0.0-py3-none-any.whl"
    ).exists()


def test_mirror_files_rejects_non_https(tmp_path):
    projects = mirror_projects()
    projects["mirrored"][0]["api_url"] = "http://api.github.com/insecure"
    with pytest.raises(index.MirrorError, match="non-https"):
        index.mirror_files(projects, tmp_path, "tok", opener=make_opener())


def test_mirror_files_missing_api_url(tmp_path):
    projects = mirror_projects()
    projects["mirrored"][0]["api_url"] = ""
    with pytest.raises(index.MirrorError, match="no API download URL"):
        index.mirror_files(projects, tmp_path, "tok", opener=make_opener())


def test_mirror_files_rejects_unsafe_filename(tmp_path):
    projects = mirror_projects()
    projects["mirrored"][0]["filename"] = "../escape-1.0.0-py3-none-any.whl"
    with pytest.raises(index.MirrorError, match="unsafe path"):
        index.mirror_files(projects, tmp_path, "tok", opener=make_opener())


def test_mirror_files_rejects_unsafe_project_key(tmp_path):
    site = tmp_path / "site"
    for hostile_key in ("../../esc", str(tmp_path / "abs-esc")):
        projects = {hostile_key: [mirror_projects()["mirrored"][0]]}
        with pytest.raises(index.MirrorError, match="unsafe path"):
            index.mirror_files(projects, site, "tok", opener=make_opener())
    # nothing was written anywhere — inside the site or beside it
    assert list(tmp_path.iterdir()) == []


def test_mirror_files_midstream_failure_preserves_cache(tmp_path):
    projects = mirror_projects()
    dest = tmp_path / "files" / "mirrored" / "mirrored-7.0.0-py3-none-any.whl"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"cached-good")
    # expected hash matches neither the cache nor the stream → re-download
    projects["mirrored"][0]["sha256"] = hashlib.sha256(b"wheel-bytes").hexdigest()

    class ExplodingResponse:
        def __init__(self):
            self.calls = 0

        def read(self, size):
            self.calls += 1
            if self.calls > 1:
                raise OSError("connection reset")
            return b"first-chunk"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    with pytest.raises(OSError, match="connection reset"):
        index.mirror_files(
            projects,
            tmp_path,
            "tok",
            opener=lambda request, timeout=None: ExplodingResponse(),
        )
    assert dest.read_bytes() == b"cached-good"  # cached copy untouched
    assert not dest.with_name(dest.name + ".part").exists()


def test_mirror_files_truncated_download(tmp_path):
    projects = mirror_projects()

    class TruncatedResponse(io.BytesIO):
        headers = {"Content-Length": "999"}

    with pytest.raises(index.MirrorError, match="truncated"):
        index.mirror_files(
            projects,
            tmp_path,
            "tok",
            opener=lambda request, timeout=None: TruncatedResponse(b"short"),
        )
    dest = tmp_path / "files" / "mirrored" / "mirrored-7.0.0-py3-none-any.whl"
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


def test_write_site_after_mirror(tmp_path):
    projects = mirror_projects()
    index.mirror_files(projects, tmp_path, "tok", opener=make_opener())
    index.write_site(projects, tmp_path, title="T", index_url=None)
    page = (tmp_path / "simple" / "mirrored" / "index.html").read_text()
    assert 'href="../../files/mirrored/mirrored-7.0.0-py3-none-any.whl#sha256=' in page
    data = json.loads((tmp_path / "simple" / "mirrored" / "index.json").read_text())
    assert (
        data["files"][0]["url"]
        == "../../files/mirrored/mirrored-7.0.0-py3-none-any.whl"
    )


def test_version_from_filename():
    assert index.version_from_filename("demo_lib-1.0.0-py3-none-any.whl") == "1.0.0"
    assert index.version_from_filename("demo_lib-1.0.0.tar.gz") == "1.0.0"
    assert index.version_from_filename("demo-lib-2.0rc1.tar.gz") == "2.0rc1"
    assert index.version_from_filename("release-notes.txt") is None
    assert index.version_from_filename("noversion.whl") is None
    assert index.version_from_filename("noversion.tar.gz") is None
    assert index.version_from_filename("foo-1.0-1-py3-none-any.whl") == "1.0"


def test_collect_projects_captures_size_and_upload_time():
    release = {
        "tag_name": "v3.0.0",
        "assets": [
            {
                "name": "sized-3.0.0-py3-none-any.whl",
                "browser_download_url": "https://github.com/o/r/releases/download/v3.0.0/sized-3.0.0-py3-none-any.whl",
                "digest": "sha256:beef",
                "size": 4321,
                "created_at": "2026-08-05T03:07:33Z",
            },
        ],
    }
    entry = index.collect_projects([release], hash_url=never_hash)["sized"][0]
    assert entry["size"] == 4321
    assert entry["upload_time"] == "2026-08-05T03:07:33Z"


def test_collect_projects_defaults_size_and_upload_time():
    entry = index.collect_projects(FIXTURE_RELEASES, hash_url=fake_hash)[
        "ghr-pypi-demo-lib"
    ][0]
    assert entry["size"] == 0
    assert entry["upload_time"] is None


def test_write_site_json_project_page(tmp_path):
    projects = index.collect_projects(FIXTURE_RELEASES, hash_url=fake_hash)
    index.write_site(projects, tmp_path, title="T", index_url=None)
    data = json.loads(
        (tmp_path / "simple" / "ghr-pypi-demo-lib" / "index.json").read_text()
    )
    assert data["meta"] == {"api-version": "1.1"}
    assert data["name"] == "ghr-pypi-demo-lib"
    assert data["versions"] == ["1.0.0"]
    files = {f["filename"]: f for f in data["files"]}
    whl = files["ghr_pypi_demo_lib-1.0.0-py3-none-any.whl"]
    assert whl["hashes"] == {"sha256": "cafef00d"}
    assert whl["size"] == 0
    assert "upload-time" not in whl
    assert "api_url" not in whl


def test_write_site_json_root(tmp_path):
    projects = index.collect_projects(FIXTURE_RELEASES, hash_url=fake_hash)
    index.write_site(projects, tmp_path, title="T", index_url=None)
    data = json.loads((tmp_path / "simple" / "index.json").read_text())
    assert data["meta"] == {"api-version": "1.1"}
    assert {"name": "ghr-pypi-demo-lib"} in data["projects"]
    assert {"name": "ghr-pypi-demo-app"} in data["projects"]


def test_write_site_json_empty_hashes_and_upload_time(tmp_path):
    projects = index.collect_projects(
        DIGEST_RELEASE, hash_url=never_hash, missing_digest="no-fragment"
    )
    index.write_site(projects, tmp_path, title="T", index_url=None)
    data = json.loads((tmp_path / "simple" / "legacy" / "index.json").read_text())
    assert data["files"][0]["hashes"] == {}
    assert "upload-time" not in data["files"][0]


def test_write_site_formats_html_only(tmp_path):
    projects = index.collect_projects(FIXTURE_RELEASES, hash_url=fake_hash)
    index.write_site(projects, tmp_path, title="T", index_url=None, formats=("html",))
    assert not list(tmp_path.rglob("*.json"))
    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "simple" / "index.html").exists()


METADATA_RELEASE = [
    {
        "tag_name": "v8.0.0",
        "_source_repo": "o/meta",
        "assets": [
            {
                "name": "hasmeta-8.0.0-py3-none-any.whl",
                "browser_download_url": "https://github.com/o/meta/releases/download/v8.0.0/hasmeta-8.0.0-py3-none-any.whl",
                "digest": "sha256:aaaa",
            },
            {
                "name": "hasmeta-8.0.0-py3-none-any.whl.metadata",
                "browser_download_url": "https://github.com/o/meta/releases/download/v8.0.0/hasmeta-8.0.0-py3-none-any.whl.metadata",
                "digest": "sha256:bbbb",
            },
            {
                "name": "nudemeta-8.0.0-py3-none-any.whl",
                "browser_download_url": "https://github.com/o/meta/releases/download/v8.0.0/nudemeta-8.0.0-py3-none-any.whl",
                "digest": "sha256:cccc",
            },
            {
                "name": "nudemeta-8.0.0-py3-none-any.whl.metadata",
                "browser_download_url": "https://github.com/o/meta/releases/download/v8.0.0/nudemeta-8.0.0-py3-none-any.whl.metadata",
            },
            {
                "name": "nometa-8.0.0-py3-none-any.whl",
                "browser_download_url": "https://github.com/o/meta/releases/download/v8.0.0/nometa-8.0.0-py3-none-any.whl",
                "digest": "sha256:dddd",
            },
            {
                "name": "withmeta-8.0.0.tar.gz",
                "browser_download_url": "https://github.com/o/meta/releases/download/v8.0.0/withmeta-8.0.0.tar.gz",
                "digest": "sha256:eeee",
            },
            {
                "name": "withmeta-8.0.0.tar.gz.metadata",
                "browser_download_url": "https://github.com/o/meta/releases/download/v8.0.0/withmeta-8.0.0.tar.gz.metadata",
                "digest": "sha256:ffff",
            },
        ],
    },
]


def test_collect_projects_pairs_metadata_assets():
    projects = index.collect_projects(METADATA_RELEASE, hash_url=never_hash)
    assert projects["hasmeta"][0]["core_metadata"] == "bbbb"
    assert projects["nudemeta"][0]["core_metadata"] is True
    assert projects["nometa"][0]["core_metadata"] is False
    assert projects["withmeta"][0]["core_metadata"] is False  # sdists excluded


def test_collect_projects_source_repo():
    projects = index.collect_projects(METADATA_RELEASE, hash_url=never_hash)
    assert projects["hasmeta"][0]["source_repo"] == "o/meta"
    untagged = index.collect_projects(FIXTURE_RELEASES, hash_url=fake_hash)
    assert untagged["ghr-pypi-demo-lib"][0]["source_repo"] == ""


def test_collect_projects_metadata_disabled():
    projects = index.collect_projects(
        METADATA_RELEASE, hash_url=never_hash, metadata=False
    )
    # even the digest-bearing pair is ignored — nothing is advertised
    assert projects["hasmeta"][0]["core_metadata"] is False
    assert all(
        entry["core_metadata"] is False
        for files in projects.values()
        for entry in files
    )


def test_collect_projects_unsafe_metadata_asset_not_paired(capsys):
    release = {
        "tag_name": "v8.0.1",
        "assets": [
            {
                "name": "victim-8.0.1-py3-none-any.whl",
                "browser_download_url": "https://github.com/o/r/releases/download/v8.0.1/victim-8.0.1-py3-none-any.whl",
                "digest": "sha256:aaaa",
            },
            {
                "name": "../victim-8.0.1-py3-none-any.whl.metadata",
                "browser_download_url": "https://github.com/o/r/releases/download/v8.0.1/evil",
                "digest": "sha256:bbbb",
            },
        ],
    }
    projects = index.collect_projects([release], hash_url=never_hash)
    assert projects["victim"][0]["core_metadata"] is False


def build_wheel_bytes(
    metadata: bytes = b"Metadata-Version: 2.1\nName: mirrored\nVersion: 7.0.0\n",
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mirrored/__init__.py", "")
        archive.writestr("mirrored-7.0.0.dist-info/METADATA", metadata)
        archive.writestr("mirrored-7.0.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")
    return buffer.getvalue()


def test_extract_metadata(tmp_path):
    wheel_bytes = build_wheel_bytes()
    projects = mirror_projects()
    index.mirror_files(
        projects, tmp_path, "tok", opener=make_opener(payload=wheel_bytes)
    )
    index.extract_metadata(projects, tmp_path)
    entry = projects["mirrored"][0]
    meta_path = (
        tmp_path / "files" / "mirrored" / "mirrored-7.0.0-py3-none-any.whl.metadata"
    )
    assert meta_path.read_bytes() == build_wheel_bytes.__defaults__[0]
    assert (
        entry["core_metadata"]
        == hashlib.sha256(build_wheel_bytes.__defaults__[0]).hexdigest()
    )

    index.write_site(projects, tmp_path, title="T", index_url=None)
    page = (tmp_path / "simple" / "mirrored" / "index.html").read_text()
    expected = f"sha256={entry['core_metadata']}"
    assert f'data-core-metadata="{expected}"' in page
    assert f'data-dist-info-metadata="{expected}"' in page
    data = json.loads((tmp_path / "simple" / "mirrored" / "index.json").read_text())
    assert data["files"][0]["core-metadata"] == {"sha256": entry["core_metadata"]}
    assert data["files"][0]["dist-info-metadata"] == {"sha256": entry["core_metadata"]}
    assert "core_metadata" not in data["files"][0]
    assert "source_repo" not in data["files"][0]


def test_extract_metadata_corrupt_wheel(tmp_path, capsys):
    projects = mirror_projects()
    index.mirror_files(
        projects, tmp_path, "tok", opener=make_opener(payload=b"not-a-zip")
    )
    index.extract_metadata(projects, tmp_path)
    assert projects["mirrored"][0]["core_metadata"] is False
    assert "cannot extract metadata" in capsys.readouterr().err
    assert not (
        tmp_path / "files" / "mirrored" / "mirrored-7.0.0-py3-none-any.whl.metadata"
    ).exists()


def build_ambiguous_wheel_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mirrored-7.0.0.dist-info/METADATA", b"one")
        archive.writestr("intruder-1.0.dist-info/METADATA", b"two")
    return buffer.getvalue()


@pytest.mark.parametrize(
    "payload,paired,error",
    [
        pytest.param(
            build_ambiguous_wheel_bytes(),
            False,
            ": no unique .dist-info/METADATA member",
            id="ambiguous-dist-info",
        ),
        pytest.param(
            b"not-a-zip",
            True,
            "cannot extract metadata",
            id="paired-but-corrupt",
        ),
    ],
)
def test_extract_metadata_failure_ladder(tmp_path, capsys, payload, paired, error):
    projects = mirror_projects()
    if paired:
        # link-mode pairing already recorded a hash from a release .metadata
        # asset — a failed extraction must reset it, not leave a stale claim
        projects["mirrored"][0]["core_metadata"] = "feedface"
    index.mirror_files(projects, tmp_path, "tok", opener=make_opener(payload=payload))
    index.extract_metadata(projects, tmp_path)
    assert projects["mirrored"][0]["core_metadata"] is False
    err = capsys.readouterr().err
    assert "cannot extract metadata" in err
    assert error in err
    assert not (
        tmp_path / "files" / "mirrored" / "mirrored-7.0.0-py3-none-any.whl.metadata"
    ).exists()


def test_read_wheel_metadata_returns_the_payload(tmp_path):
    wheel = tmp_path / "demo-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("demo-1.0.dist-info/METADATA", "Name: demo\n")
    assert index.read_wheel_metadata(wheel) == b"Name: demo\n"


def test_read_wheel_metadata_rejects_a_non_zip(tmp_path):
    wheel = tmp_path / "demo-1.0-py3-none-any.whl"
    wheel.write_bytes(b"not a zip")
    with pytest.raises(zipfile.BadZipFile):
        index.read_wheel_metadata(wheel)


def test_read_wheel_metadata_rejects_a_wheel_without_metadata(tmp_path):
    wheel = tmp_path / "demo-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("demo/__init__.py", "")
    with pytest.raises(zipfile.BadZipFile):
        index.read_wheel_metadata(wheel)


def test_read_wheel_metadata_rejects_two_metadata_members(tmp_path):
    wheel = tmp_path / "demo-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("demo-1.0.dist-info/METADATA", "Name: demo\n")
        archive.writestr("other-1.0.dist-info/METADATA", "Name: other\n")
    with pytest.raises(zipfile.BadZipFile):
        index.read_wheel_metadata(wheel)


def test_read_wheel_metadata_rejects_a_missing_file(tmp_path):
    with pytest.raises(OSError):
        index.read_wheel_metadata(tmp_path / "absent.whl")


def test_read_wheel_metadata_rejects_a_nested_metadata_member(tmp_path):
    wheel = tmp_path / "demo-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("sub/demo-1.0.dist-info/METADATA", "Name: demo\n")
    with pytest.raises(zipfile.BadZipFile):
        index.read_wheel_metadata(wheel)


def test_read_wheel_metadata_ignores_a_nested_decoy(tmp_path):
    wheel = tmp_path / "demo-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("demo-1.0.dist-info/METADATA", "Name: demo\n")
        archive.writestr("sub/demo-1.0.dist-info/METADATA", "Name: decoy\n")
    assert index.read_wheel_metadata(wheel) == b"Name: demo\n"


def test_metadata_true_renders_boolean(tmp_path):
    projects = index.collect_projects(METADATA_RELEASE, hash_url=never_hash)
    index.write_site(projects, tmp_path, title="T", index_url=None)
    page = (tmp_path / "simple" / "nudemeta" / "index.html").read_text()
    assert 'data-core-metadata="true"' in page
    data = json.loads((tmp_path / "simple" / "nudemeta" / "index.json").read_text())
    assert data["files"][0]["core-metadata"] is True
    nometa_page = (tmp_path / "simple" / "nometa" / "index.html").read_text()
    assert "data-core-metadata" not in nometa_page


def test_metadata_coverage():
    tagged = [dict(release, _source_repo="o/one") for release in FIXTURE_RELEASES]
    projects = index.collect_projects(tagged + METADATA_RELEASE, hash_url=fake_hash)
    coverage = index.metadata_coverage(projects)
    assert coverage["o/meta"] == (1, 3)  # nometa lacks metadata; 3 wheels total
    assert coverage["o/one"] == (2, 2)  # both fixture wheels lack metadata


def test_write_site_formats_json_only(tmp_path):
    projects = index.collect_projects(FIXTURE_RELEASES, hash_url=fake_hash)
    index.write_site(projects, tmp_path, title="T", index_url=None, formats=("json",))
    assert not list(tmp_path.rglob("*.html"))
    assert (tmp_path / "simple" / "index.json").exists()
    assert (tmp_path / "simple" / "ghr-pypi-demo-lib" / "index.json").exists()


FILTER_RELEASES = [
    {
        "tag_name": "yankee-v1.0.0",
        "assets": [
            {
                "name": "yankee-1.0.0-py3-none-any.whl",
                "browser_download_url": "https://github.com/o/r/releases/download/yankee-v1.0.0/yankee-1.0.0-py3-none-any.whl",
                "digest": "sha256:aaaa",
            },
            {
                "name": "yankee-1.0.0.tar.gz",
                "browser_download_url": "https://github.com/o/r/releases/download/yankee-v1.0.0/yankee-1.0.0.tar.gz",
                "digest": "sha256:bbbb",
            },
        ],
    },
    {
        "tag_name": "yankee-v1.1.0",
        "assets": [
            {
                "name": "yankee-1.1.0-py3-none-any.whl",
                "browser_download_url": "https://github.com/o/r/releases/download/yankee-v1.1.0/yankee-1.1.0-py3-none-any.whl",
                "digest": "sha256:cccc",
            },
        ],
    },
]


def test_collect_projects_filters_default_to_a_noop():
    projects = index.collect_projects(FILTER_RELEASES, hash_url=never_hash)
    assert [f["filename"] for f in projects["yankee"]] == [
        "yankee-1.0.0-py3-none-any.whl",
        "yankee-1.0.0.tar.gz",
        "yankee-1.1.0-py3-none-any.whl",
    ]
    assert all(f["yanked"] is False for f in projects["yankee"])


def test_collect_projects_excludes_a_version():
    projects = index.collect_projects(
        FILTER_RELEASES,
        hash_url=never_hash,
        filters=Filters(exclude={"yankee": ("1.0.0",)}),
    )
    assert [f["filename"] for f in projects["yankee"]] == [
        "yankee-1.1.0-py3-none-any.whl"
    ]


def test_collect_projects_exclude_drops_the_project_when_nothing_remains():
    projects = index.collect_projects(
        FILTER_RELEASES,
        hash_url=never_hash,
        filters=Filters(exclude={"Yankee": ("1.0", "1.1")}),
    )
    assert projects == {}


def test_collect_projects_excluded_file_is_never_hashed():
    # exclusion must precede the missing-digest policy too: this asset has no
    # digest, so reaching the hashing branch would blow up in never_hash
    release = {
        "tag_name": "nodigest-v3.0.0",
        "assets": [
            {
                "name": "nodigest-3.0.0-py3-none-any.whl",
                "browser_download_url": "https://github.com/o/r/releases/download/nodigest-v3.0.0/nodigest-3.0.0-py3-none-any.whl",
            },
        ],
    }
    projects = index.collect_projects(
        [release],
        hash_url=never_hash,
        filters=Filters(exclude={"nodigest": ("3.0.0",)}),
    )
    assert projects == {}


DUPLICATE_FILENAME_RELEASES = [
    {
        "tag_name": "first",
        "assets": [
            {
                "name": "dupe-1.0.0-py3-none-any.whl",
                "browser_download_url": "https://github.com/o/first/releases/download/first/dupe-1.0.0-py3-none-any.whl",
                "digest": "sha256:aaaa",
            },
        ],
    },
    {
        "tag_name": "second",
        "assets": [
            {
                "name": "dupe-1.0.0-py3-none-any.whl",
                "browser_download_url": "https://github.com/o/second/releases/download/second/dupe-1.0.0-py3-none-any.whl",
                "digest": "sha256:aaaa",
            },
        ],
    },
]


class ExcludeFirstOnly(Filters):
    """Filters that exclude only the very first file they are asked about.

    Two assets sharing a filename parse to the same project and version, so a
    configured exclusion would drop both and no data-driven fixture can tell
    "skipped before the ``seen`` bookkeeping" from "skipped after". This
    stand-in makes the ordering observable: if the excluded file claimed the
    filename slot, the second copy would be dropped as a duplicate.
    """

    def __init__(self):
        super().__init__()
        object.__setattr__(self, "calls", [])

    def is_excluded(self, project, version):
        self.calls.append((project, version))
        return len(self.calls) == 1


def test_excluded_file_does_not_claim_a_dedupe_slot(capsys):
    filters = ExcludeFirstOnly()
    projects = index.collect_projects(
        DUPLICATE_FILENAME_RELEASES, hash_url=never_hash, filters=filters
    )
    assert filters.calls == [("dupe", "1.0.0"), ("dupe", "1.0.0")]
    files = projects["dupe"]
    assert len(files) == 1
    assert "o/second" in files[0]["url"]  # the later copy filled the empty slot
    assert "duplicate asset" not in capsys.readouterr().err


def test_excluded_duplicates_warn_about_nothing(capsys):
    projects = index.collect_projects(
        DUPLICATE_FILENAME_RELEASES,
        hash_url=never_hash,
        filters=Filters(exclude={"dupe": ("1.0.0",)}),
    )
    assert projects == {}
    assert capsys.readouterr().err == ""


def test_collect_projects_yanks_with_a_reason():
    projects = index.collect_projects(
        FILTER_RELEASES,
        hash_url=never_hash,
        filters=Filters(yanked={"Yankee": {"1.0": "built from a dirty tree"}}),
    )
    yanked = {f["filename"]: f["yanked"] for f in projects["yankee"]}
    assert yanked == {
        "yankee-1.0.0-py3-none-any.whl": "built from a dirty tree",
        "yankee-1.0.0.tar.gz": "built from a dirty tree",
        "yankee-1.1.0-py3-none-any.whl": False,
    }


def test_collect_projects_yanks_without_a_reason():
    projects = index.collect_projects(
        FILTER_RELEASES,
        hash_url=never_hash,
        filters=Filters(yanked={"yankee": {"1.0.0": True}}),
    )
    entry = projects["yankee"][0]
    assert entry["yanked"] is True
    assert entry["sha256"] == "aaaa"  # otherwise processed normally


def yanked_site(tmp_path, filters):
    projects = index.collect_projects(
        FILTER_RELEASES, hash_url=never_hash, filters=filters
    )
    index.write_site(projects, tmp_path, title="T", index_url=None)
    page = (tmp_path / "simple" / "yankee" / "index.html").read_text()
    data = json.loads((tmp_path / "simple" / "yankee" / "index.json").read_text())
    return page, data


def test_write_site_yanked_with_a_reason(tmp_path):
    page, data = yanked_site(
        tmp_path, Filters(yanked={"yankee": {"1.0.0": "bad sdist"}})
    )
    assert 'data-yanked="bad sdist"' in page
    files = {f["filename"]: f for f in data["files"]}
    assert files["yankee-1.0.0-py3-none-any.whl"]["yanked"] == "bad sdist"
    assert files["yankee-1.0.0.tar.gz"]["yanked"] == "bad sdist"


def test_write_site_yanked_without_a_reason(tmp_path):
    page, data = yanked_site(tmp_path, Filters(yanked={"yankee": {"1.0.0": True}}))
    assert 'data-yanked=""' in page
    files = {f["filename"]: f for f in data["files"]}
    assert files["yankee-1.0.0-py3-none-any.whl"]["yanked"] is True


def test_write_site_yank_marks_only_the_yanked_version(tmp_path):
    page, data = yanked_site(tmp_path, Filters(yanked={"yankee": {"1.0.0": True}}))
    assert page.count("data-yanked") == 2  # the wheel and the sdist, not 1.1.0
    files = {f["filename"]: f for f in data["files"]}
    assert "yanked" not in files["yankee-1.1.0-py3-none-any.whl"]


def test_write_site_not_yanked_emits_nothing(tmp_path):
    page, data = yanked_site(tmp_path, Filters())
    assert "data-yanked" not in page
    assert all("yanked" not in file for file in data["files"])


def test_write_site_yank_preserves_hash_and_metadata_attributes(tmp_path):
    projects = index.collect_projects(
        METADATA_RELEASE,
        hash_url=never_hash,
        filters=Filters(yanked={"hasmeta": {"8.0.0": "oops"}}),
    )
    index.write_site(projects, tmp_path, title="T", index_url=None)
    page = (tmp_path / "simple" / "hasmeta" / "index.html").read_text()
    assert "#sha256=aaaa" in page
    assert 'data-dist-info-metadata="sha256=bbbb"' in page
    assert 'data-core-metadata="sha256=bbbb"' in page
    assert 'data-yanked="oops"' in page


def test_yanked_version_still_listed_but_excluded_version_is_not(tmp_path):
    _, yanked = yanked_site(tmp_path, Filters(yanked={"yankee": {"1.0.0": True}}))
    assert yanked["versions"] == ["1.0.0", "1.1.0"]
    _, excluded = yanked_site(tmp_path, Filters(exclude={"yankee": ("1.0.0",)}))
    assert excluded["versions"] == ["1.1.0"]


def test_write_site_yank_reason_is_html_escaped(tmp_path):
    # the reason is config-controlled free text landing inside an attribute --
    # pin the escaping so a stray |safe or a non-autoescaped template name
    # cannot quietly turn it into attribute injection
    reason = 'broke "everything" & <b>bold</b>'
    page, data = yanked_site(tmp_path, Filters(yanked={"yankee": {"1.0.0": reason}}))
    assert (
        'data-yanked="broke &#34;everything&#34; &amp; &lt;b&gt;bold&lt;/b&gt;"' in page
    )
    assert "<b>bold</b>" not in page
    files = {f["filename"]: f for f in data["files"]}
    assert files["yankee-1.0.0-py3-none-any.whl"]["yanked"] == reason  # JSON is raw


def test_collect_records_the_sidecar_api_url():
    releases = [
        {
            "tag_name": "v1",
            "assets": [
                {
                    "name": "demo-1.0-py3-none-any.whl",
                    "browser_download_url": "https://example/demo-1.0-py3-none-any.whl",
                    "url": "https://api.github.com/repos/o/r/releases/assets/11",
                    "digest": "sha256:" + "a" * 64,
                },
                {
                    "name": "demo-1.0-py3-none-any.whl.metadata",
                    "browser_download_url": "https://example/demo.metadata",
                    "url": "https://api.github.com/repos/o/r/releases/assets/12",
                    "digest": "sha256:" + "b" * 64,
                },
            ],
        }
    ]
    entry = index.collect_projects(releases)["demo"][0]
    assert entry["metadata_api_url"].endswith("/12")
    assert entry["core_metadata"] == "b" * 64


def test_collect_metadata_api_url_empty_without_a_sidecar():
    projects = index.collect_projects(METADATA_RELEASE, hash_url=never_hash)
    assert projects["nometa"][0]["metadata_api_url"] == ""
    # a sidecar with no API url pairs, but records nothing to redirect to
    assert projects["nudemeta"][0]["core_metadata"] is True
    assert projects["nudemeta"][0]["metadata_api_url"] == ""


def redirect_entry(
    filename="demo-1.0-py3-none-any.whl",
    asset_id="11",
    metadata_id=None,
    repo="o/r",
    core_metadata=False,
):
    """A :class:`index.FileEntry` shaped for the redirect-mode helpers."""
    root = f"https://api.github.com/repos/{repo}/releases/assets"
    return {
        "filename": filename,
        "url": "https://example/x",
        "api_url": f"{root}/{asset_id}",
        "metadata_api_url": f"{root}/{metadata_id}" if metadata_id else "",
        "sha256": None,
        "size": 0,
        "upload_time": None,
        "core_metadata": core_metadata,
        "source_repo": repo,
        "yanked": False,
    }


def test_redirect_urls_rewrites_every_entry():
    projects = {
        "demo": [
            {
                "filename": "demo-1.0-py3-none-any.whl",
                "url": "https://example/x",
                "api_url": "https://api.github.com/repos/o/r/releases/assets/11",
                "metadata_api_url": "",
                "sha256": None,
                "size": 0,
                "upload_time": None,
                "core_metadata": False,
                "source_repo": "o/r",
                "yanked": False,
            }
        ]
    }
    index.redirect_urls(projects)
    assert projects["demo"][0]["url"] == "../../_assets/11/demo-1.0-py3-none-any.whl"


def test_redirect_urls_rejects_an_entry_without_an_api_url():
    projects = {
        "demo": [
            {
                "filename": "d.whl",
                "url": "",
                "api_url": "",
                "metadata_api_url": "",
                "sha256": None,
                "size": 0,
                "upload_time": None,
                "core_metadata": False,
                "source_repo": "o/r",
                "yanked": False,
            }
        ]
    }
    with pytest.raises(index.RedirectError, match="no asset API URL"):
        index.redirect_urls(projects)


def test_redirect_urls_survives_the_site_prefix(tmp_path):
    # ../../_assets is relative to simple/<project>/, the same property
    # mirroring relies on — the site can be served from any prefix
    projects = {"demo": [redirect_entry()]}
    index.redirect_urls(projects)
    index.write_site(projects, tmp_path, title="T", index_url=None)
    data = json.loads((tmp_path / "simple" / "demo" / "index.json").read_text())
    assert data["files"][0]["url"] == "../../_assets/11/demo-1.0-py3-none-any.whl"


def test_write_manifest_lists_repo_and_filename(tmp_path):
    projects = {"demo": [redirect_entry()]}
    target = index.write_manifest(projects, tmp_path)
    assert target == tmp_path / "_assets" / "manifest.json"
    manifest = json.loads(target.read_text())
    assert manifest["version"] == 1
    assert manifest["assets"]["11"] == {
        "repo": "o/r",
        "filename": "demo-1.0-py3-none-any.whl",
    }


def test_write_manifest_records_metadata_id_only_when_paired(tmp_path):
    projects = {
        "demo": [
            redirect_entry(asset_id="11", metadata_id="12"),
            redirect_entry(filename="demo-2.0.tar.gz", asset_id="13"),
        ]
    }
    assets = json.loads(index.write_manifest(projects, tmp_path).read_text())["assets"]
    assert assets["11"]["metadata_id"] == "12"
    assert "metadata_id" not in assets["13"]


def test_write_manifest_creates_the_assets_directory(tmp_path):
    out = tmp_path / "site"
    target = index.write_manifest({"demo": [redirect_entry()]}, out)
    assert target.is_file()
    assert target.relative_to(out).as_posix() == "_assets/manifest.json"


@pytest.mark.parametrize(
    "api_url",
    [
        "https://api.github.com/repos/o/r/releases/assets/11?ref=main",
        "https://api.github.com/repos/o/r/releases/assets/..",
        "https://api.github.com/repos/o/r/releases/assets/",
        "https://api.github.com/repos/o/r/releases/assets/11a",
        "",
    ],
    ids=["query-string", "traversal", "empty-segment", "non-numeric", "empty"],
)
def test_asset_id_requires_a_numeric_id(api_url):
    with pytest.raises(index.RedirectError, match="numeric asset id"):
        index._asset_id(api_url)


def test_asset_id_tolerates_a_trailing_slash():
    assert index._asset_id("https://api.github.com/repos/o/r/assets/11/") == "11"


def test_redirect_urls_rejects_a_traversal_asset_id():
    projects = {"demo": [redirect_entry(asset_id="..")]}
    with pytest.raises(index.RedirectError, match="numeric asset id"):
        index.redirect_urls(projects)
    assert projects["demo"][0]["url"] == "https://example/x"  # never rewritten


def test_write_manifest_rejects_an_entry_without_an_asset_id(tmp_path):
    # collect_projects already anticipates an asset payload with no 'url'
    projects = {"demo": [dict(redirect_entry(), api_url="")]}
    with pytest.raises(index.RedirectError, match="numeric asset id"):
        index.write_manifest(projects, tmp_path)


def test_write_manifest_rejects_a_duplicate_asset_id(tmp_path):
    # last-wins would silently drop one file from the allow-list while its
    # index URL stayed published — un-servable rather than merely absent
    projects = {
        "demo": [
            redirect_entry(filename="demo-1.0-py3-none-any.whl", asset_id="11"),
            redirect_entry(filename="demo-2.0-py3-none-any.whl", asset_id="11"),
        ]
    }
    with pytest.raises(index.RedirectError, match="already claimed"):
        index.write_manifest(projects, tmp_path)


def test_write_manifest_is_byte_stable(tmp_path):
    # republished on every build, so the artifact has to be diffable
    def build(name):
        out = tmp_path / name
        return index.write_manifest(
            {
                "demo": [redirect_entry(asset_id="2", metadata_id="3")],
                "other": [redirect_entry(filename="other-1.0.tar.gz", asset_id="1")],
            },
            out,
        ).read_bytes()

    assert build("first") == build("second")


def test_write_manifest_sorts_asset_ids(tmp_path):
    projects = {
        "demo": [redirect_entry(filename="demo-2.0.tar.gz", asset_id="2")],
        "other": [redirect_entry(filename="other-1.0.tar.gz", asset_id="1")],
    }
    manifest = json.loads(index.write_manifest(projects, tmp_path).read_text())
    # json.loads preserves document order, so this pins sort_keys, not insertion
    assert list(manifest["assets"]) == ["1", "2"]


def test_write_manifest_covers_every_project(tmp_path):
    projects = {
        "one": [redirect_entry(filename="one-1.0.tar.gz", asset_id="1", repo="o/one")],
        "two": [redirect_entry(filename="two-1.0.tar.gz", asset_id="2", repo="o/two")],
    }
    assets = json.loads(index.write_manifest(projects, tmp_path).read_text())["assets"]
    assert sorted(assets) == ["1", "2"]
    assert assets["2"]["repo"] == "o/two"


def counting_opener(payload, log):
    def opener(request, timeout=None):
        log.append(request)
        return io.BytesIO(payload)

    return opener


def exploding_opener(request, timeout=None):
    raise AssertionError(f"opener called for {request.get_full_url()}")


WHEEL_METADATA = b"Metadata-Version: 2.1\nName: mirrored\nVersion: 7.0.0\n"


def test_extract_missing_metadata_skips_paired_wheels(tmp_path):
    projects = {"demo": [redirect_entry(metadata_id="12", core_metadata="bbbb")]}
    index.extract_missing_metadata(projects, tmp_path, "tok", opener=exploding_opener)
    assert projects["demo"][0]["core_metadata"] == "bbbb"
    assert not (tmp_path / "_assets").exists()


def test_extract_missing_metadata_skips_sdists(tmp_path):
    projects = {"demo": [redirect_entry(filename="demo-1.0.tar.gz")]}
    index.extract_missing_metadata(projects, tmp_path, "tok", opener=exploding_opener)
    assert projects["demo"][0]["core_metadata"] is False


def test_extract_missing_metadata_downloads_an_unpaired_wheel(tmp_path):
    log = []
    projects = {"demo": [redirect_entry()]}
    index.extract_missing_metadata(
        projects,
        tmp_path,
        "tok",
        opener=counting_opener(build_wheel_bytes(), log),
    )
    sidecar = tmp_path / "_assets" / "11" / "demo-1.0-py3-none-any.whl.metadata"
    assert sidecar.read_bytes() == WHEEL_METADATA
    assert (
        projects["demo"][0]["core_metadata"]
        == hashlib.sha256(WHEEL_METADATA).hexdigest()
    )
    assert len(log) == 1  # downloaded exactly once
    assert (
        log[0].get_full_url() == "https://api.github.com/repos/o/r/releases/assets/11"
    )
    assert log[0].get_header("Accept") == "application/octet-stream"
    assert log[0].get_header("Authorization") == "Bearer tok"


def test_extract_missing_metadata_rejects_a_non_https_url(tmp_path):
    # the request carries a bearer token — a plaintext scheme must not get one
    projects = {"demo": [dict(redirect_entry(), api_url="http://evil.example/x/1")]}
    with pytest.raises(index.RedirectError, match="non-https"):
        index.extract_missing_metadata(
            projects, tmp_path, "tok", opener=exploding_opener
        )


def test_extract_missing_metadata_rejects_a_missing_api_url(tmp_path):
    projects = {"demo": [dict(redirect_entry(), api_url="")]}
    with pytest.raises(index.RedirectError, match="no asset API URL"):
        index.extract_missing_metadata(
            projects, tmp_path, "tok", opener=exploding_opener
        )


def test_extract_missing_metadata_rejects_a_traversal_asset_id(tmp_path):
    # the id is a directory name — '..' would place the sidecar outside _assets
    projects = {"demo": [redirect_entry(asset_id="..")]}
    with pytest.raises(index.RedirectError, match="numeric asset id"):
        index.extract_missing_metadata(
            projects, tmp_path, "tok", opener=exploding_opener
        )
    assert list(tmp_path.iterdir()) == []  # and nothing was downloaded or written


def test_extract_missing_metadata_warns_on_a_corrupt_download(tmp_path, capsys):
    # 'feedface' is synthetic: collect_projects never pairs a hash with an
    # empty metadata_api_url. Starting from a realistic False would make the
    # assertion vacuous, so the sentinel is what proves the reset happened.
    projects = {"demo": [redirect_entry(core_metadata="feedface")]}
    index.extract_missing_metadata(
        projects, tmp_path, "tok", opener=counting_opener(b"not-a-zip", [])
    )
    assert projects["demo"][0]["core_metadata"] is False  # reset, as extract_metadata
    assert "cannot extract metadata" in capsys.readouterr().err
    assert not (
        tmp_path / "_assets" / "11" / "demo-1.0-py3-none-any.whl.metadata"
    ).exists()


@pytest.mark.parametrize(
    "payload", [build_wheel_bytes(), b"not-a-zip"], ids=["wheel", "corrupt"]
)
def test_extract_missing_metadata_leaves_no_temporary_file(tmp_path, payload, capsys):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    projects = {"demo": [redirect_entry()]}
    saved = tempfile.tempdir
    tempfile.tempdir = str(scratch)
    try:
        index.extract_missing_metadata(
            projects, tmp_path, "tok", opener=counting_opener(payload, [])
        )
    finally:
        tempfile.tempdir = saved
    capsys.readouterr()
    assert list(scratch.iterdir()) == []


def test_redirect_metadata_url_is_the_sidecar_path(tmp_path):
    # pip appends nothing: it fetches <file-url>.metadata, so the extracted
    # sidecar must sit exactly beside the redirect path
    projects = {"demo": [redirect_entry()]}
    index.extract_missing_metadata(
        projects, tmp_path, "tok", opener=counting_opener(build_wheel_bytes(), [])
    )
    index.redirect_urls(projects)
    url = projects["demo"][0]["url"]
    assert (tmp_path / (url.removeprefix("../../") + ".metadata")).is_file()
