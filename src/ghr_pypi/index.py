"""Build a PEP 503 "simple" package index from GitHub release assets.

Lists every release in a GitHub repository, collects the ``.whl`` and
``.tar.gz`` assets, and writes a static PyPI-compatible index that GitHub
Pages can serve. Links point at the release assets' download URLs and, when a
hash is available, carry ``#sha256=`` fragments so pip verifies the download.
"""

import hashlib
import json
import re
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict

from jinja2 import (
    ChoiceLoader,
    Environment,
    FileSystemLoader,
    PackageLoader,
    PrefixLoader,
    select_autoescape,
)
from packaging.version import InvalidVersion, Version

from ghr_pypi.config import NO_FILTERS, Filters, Formats, MissingDigest

API_ROOT = "https://api.github.com"


class FileEntry(TypedDict):
    """A release asset file with download URL, hash, and metadata.

    ``sha256`` is the hash, or None when unavailable and the policy allows it.
    ``size`` comes from the GitHub asset API, 0 when unknown. ``upload_time``
    is the asset's RFC 3339 ``created_at``, None when unknown. ``api_url`` is
    the asset's API endpoint, used for authenticated mirror downloads; not
    emitted. ``core_metadata`` is the PEP 658 core metadata: its sha256, True
    when available unhashed, False when absent. ``metadata_api_url`` is the
    sidecar's asset API endpoint, empty when the release has none; internal,
    not emitted. ``source_repo`` is the OWNER/NAME the entry came from, for
    diagnostics; not emitted. ``yanked`` is the PEP 592 status: False when not
    yanked, else True or the reason.
    """

    filename: str
    url: str
    sha256: str | None
    size: int
    upload_time: str | None
    api_url: str
    core_metadata: str | bool
    metadata_api_url: str
    source_repo: str
    yanked: str | bool


Projects = dict[str, list[FileEntry]]


def build_env(templates_dir: Path | None = None) -> Environment:
    """Return the Jinja environment, checking ``templates_dir`` first.

    Built-in templates stay reachable under a ``builtin/`` prefix so an
    override can ``{% extends "builtin/landing.html" %}`` without recursing
    into itself.
    """
    builtin = PackageLoader("ghr_pypi")
    loaders: list = []
    if templates_dir is not None:
        loaders.append(FileSystemLoader(templates_dir))
    loaders += [PrefixLoader({"builtin": builtin}), builtin]
    return Environment(
        loader=ChoiceLoader(loaders),
        autoescape=select_autoescape(("html",)),
        keep_trailing_newline=True,
    )


def normalize(name: str) -> str:
    """Normalize a project name per PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


def project_name_from_filename(filename: str) -> str | None:
    """Return the project name for a wheel or sdist filename, else None."""
    if filename.endswith(".whl"):
        return filename.split("-")[0]
    if filename.endswith(".tar.gz"):
        return filename[: -len(".tar.gz")].rsplit("-", 1)[0]
    return None


def version_from_filename(filename: str) -> str | None:
    """Return the version encoded in a wheel or sdist filename, else None."""
    if filename.endswith(".whl"):
        parts = filename[: -len(".whl")].split("-")
        return parts[1] if len(parts) > 1 else None
    if filename.endswith(".tar.gz"):
        stem = filename[: -len(".tar.gz")]
        if "-" in stem:
            return stem.rsplit("-", 1)[1]
    return None


class PaginationError(Exception):
    """Raised when a paginated GitHub endpoint never returns a short page."""


_PER_PAGE = 100
_MAX_PAGES = 100


def _paginate(
    url: str,
    token: str,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> list[dict[str, Any]]:
    """Return every item of a paginated GitHub list endpoint.

    Requests pages of ``_PER_PAGE`` until one comes back short. The
    ``_MAX_PAGES`` cap exists because a server that always returns a full page
    would otherwise spin forever; hitting it raises rather than silently
    truncating, since a short index is worse than a failed build.
    """
    if not url.startswith("https://"):
        raise ValueError(f"refusing to fetch non-https URL: {url}")
    items: list[dict[str, Any]] = []
    for page in range(1, _MAX_PAGES + 1):
        request = urllib.request.Request(
            f"{url}?per_page={_PER_PAGE}&page={page}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
            },
        )
        with opener(  # nosec B310 — scheme validated above
            request, timeout=30
        ) as response:
            batch = json.load(response)
        items.extend(batch)
        if len(batch) < _PER_PAGE:
            return items
    raise PaginationError(
        f"{url}: more than {_MAX_PAGES * _PER_PAGE} items; refusing to page further"
    )


def fetch_releases(
    repo: str,
    token: str,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> list[dict[str, Any]]:
    """Return the JSON list of every release for the ``owner/name`` repository."""
    return _paginate(f"{API_ROOT}/repos/{repo}/releases", token, opener=opener)


def fetch_repositories(
    owner: str,
    token: str,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> list[str]:
    """Return every ``owner/name`` repository the token can read.

    Tries the organization endpoint and falls back to the user endpoint on 404.
    The user endpoint lists only *public* repositories — GitHub has no endpoint
    for another account's private ones — so private repositories on a personal
    account have to be listed explicitly.
    """
    try:
        payload = _paginate(f"{API_ROOT}/orgs/{owner}/repos", token, opener=opener)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        payload = _paginate(f"{API_ROOT}/users/{owner}/repos", token, opener=opener)
    return [repo["full_name"] for repo in payload]


def hash_url(url: str) -> str:
    """Download ``url`` and return the sha256 hex digest of its content."""
    if not url.startswith("https://"):
        raise ValueError(f"refusing to fetch non-https URL: {url}")
    digest = hashlib.sha256()
    with urllib.request.urlopen(  # nosec B310 — scheme validated above
        url, timeout=30
    ) as response:
        for chunk in iter(lambda: response.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unsafe_name(name: str) -> bool:
    return "/" in name or "\\" in name or ":" in name or name.startswith(".")


def _sha256_digest(asset: dict[str, Any]) -> str | None:
    """Return the asset's sha256 from its API digest, or None."""
    digest = asset.get("digest")
    if isinstance(digest, str) and digest.startswith("sha256:"):
        return digest[len("sha256:") :] or None
    return None


def collect_projects(
    releases: list[dict[str, Any]],
    hash_url: Callable[[str], str] = hash_url,
    missing_digest: MissingDigest = "download",
    defer_hash: bool = False,
    metadata: bool = True,
    filters: Filters = NO_FILTERS,
) -> Projects:
    """Map normalized project names to their release files.

    Returns ``{project: [entry, ...]}`` sorted by project name and filename;
    each entry carries ``filename``, ``url``, ``sha256``, ``size``,
    ``upload_time``, ``api_url``, ``core_metadata``, ``metadata_api_url``,
    ``source_repo``, and
    ``yanked`` (see :class:`FileEntry`). Assets that are not wheels or sdists are
    ignored, as are draft releases (their assets aren't publicly
    downloadable). Duplicate filenames across releases are indexed once
    (first occurrence wins) with a stderr warning.

    With ``metadata`` False, ``.metadata`` assets are never paired and every
    entry's ``core_metadata`` is False — nothing is advertised.

    Assets carrying an API ``digest`` are never downloaded; ``missing_digest``
    governs the rest: ``download`` (hash them), ``no-fragment`` (index without
    a hash), ``omit`` (exclude with a warning). With ``defer_hash`` no hashing
    happens at all — sha256 is the API digest or None and ``missing_digest``
    does not apply (mirroring computes hashes from the downloaded bytes).

    ``filters`` carries the configured yank and exclusion rules: excluded
    files are dropped before anything else looks at them (so they never claim
    a filename slot a later copy could fill), and yanked files are indexed
    normally with their PEP 592 reason on ``yanked``.
    """
    projects: Projects = {}
    seen: set[str] = set()
    for release in releases:
        if release.get("draft"):
            continue
        source_repo = release.get("_source_repo", "")
        # pair .metadata assets per release — the pair must live at
        # <wheel-url>.metadata, so cross-release pairing would advertise 404s
        metadata_assets: dict[str, dict[str, Any]] = {}
        if metadata:
            for asset in release.get("assets", []):
                name = asset["name"]
                if not name.endswith(".metadata") or _unsafe_name(name):
                    continue
                metadata_assets[name[: -len(".metadata")]] = asset
        for asset in release.get("assets", []):
            name = asset["name"]
            if _unsafe_name(name):
                print(
                    f"warning: skipping asset with unsafe name {name!r}",
                    file=sys.stderr,
                )
                continue
            project = project_name_from_filename(name)
            if project is None:
                continue
            version = version_from_filename(name)
            # before the duplicate bookkeeping: an excluded file must not
            # claim the filename slot another repository's copy could fill
            if filters.is_excluded(project, version):
                continue
            if name in seen:
                print(
                    f"warning: duplicate asset {name} ignored "
                    f"({asset['browser_download_url']})",
                    file=sys.stderr,
                )
                continue
            seen.add(name)
            sha256 = _sha256_digest(asset)  # API digest wins; never download
            if sha256 is None and not (defer_hash or missing_digest == "no-fragment"):
                # defer_hash short-circuits policy; mirroring hashes downloaded bytes
                if missing_digest == "omit":
                    print(
                        f"warning: {name} has no digest, omitted (missing_digest=omit)",
                        file=sys.stderr,
                    )
                    continue
                sha256 = hash_url(asset["browser_download_url"])
            core_metadata: str | bool = False
            metadata_api_url = ""
            if name.endswith(".whl") and name in metadata_assets:
                sidecar = metadata_assets[name]
                core_metadata = _sha256_digest(sidecar) or True
                metadata_api_url = sidecar.get("url") or ""
            projects.setdefault(normalize(project), []).append(
                {
                    "filename": name,
                    "url": asset["browser_download_url"],
                    "sha256": sha256,
                    "size": int(asset.get("size") or 0),
                    "upload_time": asset.get("created_at"),
                    "api_url": asset.get("url") or "",
                    "core_metadata": core_metadata,
                    "metadata_api_url": metadata_api_url,
                    "source_repo": source_repo,
                    "yanked": filters.yank_reason(project, version),
                }
            )
    for files in projects.values():
        files.sort(key=lambda file: file["filename"])
    return dict(sorted(projects.items()))


class MirrorError(RuntimeError):
    """Raised when mirroring an asset fails.

    Covers unsafe paths, missing or non-https API URLs, truncated downloads,
    and downloads that do not match their advertised digest.
    """


class RedirectError(RuntimeError):
    """Raised when an entry cannot be served through the redirector."""


_ASSET_ID = re.compile(r"[0-9]+")


def _asset_id(api_url: str) -> str:
    """Return the trailing asset id of a GitHub asset API URL.

    GitHub asset ids are integers, and this one becomes both a manifest key
    and a directory name — so the shape is checked rather than trusted. A
    ``..`` segment would escape ``_assets`` and a query string would fork one
    asset into two allow-list entries; neither is a shape GitHub emits, but
    the name half of the same payload is hard-validated by ``_unsafe_name``
    and the id half deserves the same.
    """
    asset_id = api_url.rstrip("/").rsplit("/", 1)[-1]
    if not _ASSET_ID.fullmatch(asset_id):
        raise RedirectError(f"{api_url!r} does not end in a numeric asset id")
    return asset_id


def redirect_urls(projects: Projects) -> None:
    """Point every entry at the site's own redirector path.

    ``../../_assets/<id>/<filename>`` is relative to ``simple/<project>/``, so
    the site can be served from any prefix — the property mirroring relies on
    too.
    """
    for files in projects.values():
        for entry in files:
            if not entry["api_url"]:
                raise RedirectError(
                    f"{entry['filename']} has no asset API URL; "
                    "redirect mode cannot serve it"
                )
            entry["url"] = (
                f"../../_assets/{_asset_id(entry['api_url'])}/{entry['filename']}"
            )


def write_manifest(projects: Projects, out_dir: Path) -> Path:
    """Write the redirector's allow-list of published assets.

    The redirector serves only what this lists, so a leaked index URL cannot be
    turned into a fetch of any asset the token happens to be able to read.

    A collision is refused rather than resolved last-wins: the dropped entry
    would keep its published index URL and become un-servable, which is a
    worse failure than a loud one at build time.
    """
    assets: dict[str, dict[str, Any]] = {}
    for files in projects.values():
        for entry in files:
            record: dict[str, Any] = {
                "repo": entry["source_repo"],
                "filename": entry["filename"],
            }
            if entry["metadata_api_url"]:
                record["metadata_id"] = _asset_id(entry["metadata_api_url"])
            asset_id = _asset_id(entry["api_url"])
            if asset_id in assets:
                raise RedirectError(
                    f"{entry['filename']}: asset id {asset_id} is already "
                    f"claimed by {assets[asset_id]['filename']}; the manifest "
                    "cannot list both"
                )
            assets[asset_id] = record
    target = out_dir / "_assets" / "manifest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"version": 1, "assets": assets}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def mirror_files(
    projects: Projects,
    out_dir: Path,
    token: str,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> None:
    """Download every file into ``out_dir/files`` and relink entries.

    Files already present with the expected sha256 are reused (when no
    digest was advertised, the existing file's hash is adopted). Downloads
    go through the asset's authenticated API endpoint, hash while
    streaming into a ``.part`` file that replaces the destination only
    after verification (Content-Length when present, then the advertised
    digest) — a failure removes the partial file and leaves any previously
    cached copy untouched, raising ``MirrorError``. Entry URLs are
    rewritten to site-relative paths.
    """
    files_root = (out_dir / "files").resolve()
    for project, files in projects.items():
        project_dir = out_dir / "files" / project
        for entry in files:
            relative_url = f"../../files/{project}/{entry['filename']}"
            dest = project_dir / entry["filename"]
            if (
                not dest.resolve().is_relative_to(files_root)
                or dest.parent.resolve() != project_dir.resolve()
            ):
                raise MirrorError(f"{entry['filename']}: unsafe path")
            if dest.is_file():
                existing = _hash_file(dest)
                if entry["sha256"] is None or existing == entry["sha256"]:
                    entry["sha256"] = existing
                    entry["url"] = relative_url
                    continue
            if not entry["api_url"]:
                raise MirrorError(f"{entry['filename']}: asset has no API download URL")
            if not entry["api_url"].startswith("https://"):
                raise MirrorError(
                    f"{entry['filename']}: refusing to fetch non-https URL: "
                    f"{entry['api_url']!r}"
                )
            project_dir.mkdir(parents=True, exist_ok=True)
            request = urllib.request.Request(
                entry["api_url"],
                headers={
                    "Accept": "application/octet-stream",
                    "Authorization": f"Bearer {token}",
                },
            )
            digest = hashlib.sha256()
            written = 0
            tmp = dest.with_name(dest.name + ".part")
            try:
                with (
                    opener(  # nosec B310 — scheme validated above
                        request, timeout=30
                    ) as response,
                    tmp.open("wb") as sink,
                ):
                    for chunk in iter(lambda: response.read(65536), b""):
                        digest.update(chunk)
                        written += len(chunk)
                        sink.write(chunk)
                expected = getattr(response, "headers", {}).get("Content-Length")
                try:
                    expected_len = int(expected) if expected is not None else None
                except ValueError:
                    expected_len = None  # garbage Content-Length → treat as absent
                if expected_len is not None and written != expected_len:
                    raise MirrorError(
                        f"{entry['filename']}: truncated download "
                        f"({written} of {expected_len} bytes)"
                    )
                computed = digest.hexdigest()
                if entry["sha256"] is not None and computed != entry["sha256"]:
                    raise MirrorError(
                        f"{entry['filename']}: downloaded sha256 {computed} does "
                        f"not match advertised digest {entry['sha256']}"
                    )
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            tmp.replace(dest)
            entry["sha256"] = computed
            entry["url"] = relative_url


def read_wheel_metadata(path: Path) -> bytes:
    """Return a wheel's :pep:`658` core metadata.

    Reads the single top-level ``*.dist-info/METADATA`` member. Raises
    ``OSError`` when the file cannot be read and ``zipfile.BadZipFile`` when it
    is not a valid zip or does not carry exactly one such member.
    """
    with zipfile.ZipFile(path) as archive:
        members = [
            member
            for member in archive.namelist()
            if member.endswith(".dist-info/METADATA") and member.count("/") == 1
        ]
        if len(members) != 1:
            raise zipfile.BadZipFile("no unique .dist-info/METADATA member")
        return archive.read(members[0])


def metadata_path(wheel: Path) -> Path:
    """Return the :pep:`658` sidecar path for a wheel."""
    return wheel.with_name(wheel.name + ".metadata")


def extract_metadata(projects: Projects, out_dir: Path) -> None:
    """Extract PEP 658 core metadata from mirrored wheels.

    Reads ``*.dist-info/METADATA`` from each mirrored ``.whl``, writes it
    beside the wheel as ``<filename>.metadata``, and records its sha256 on
    the entry. Extraction failures warn and leave the entry without
    metadata.
    """
    for project, files in projects.items():
        for entry in files:
            if not entry["filename"].endswith(".whl"):
                continue
            wheel = out_dir / "files" / project / entry["filename"]
            try:
                payload = read_wheel_metadata(wheel)
            except (OSError, zipfile.BadZipFile) as error:
                print(
                    f"warning: cannot extract metadata from "
                    f"{entry['filename']}: {error}",
                    file=sys.stderr,
                )
                entry["core_metadata"] = False
                continue
            metadata_path(wheel).write_bytes(payload)
            entry["core_metadata"] = hashlib.sha256(payload).hexdigest()


def extract_missing_metadata(
    projects: Projects,
    out_dir: Path,
    token: str,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> None:
    """Write ``.metadata`` for wheels whose release carries no sidecar.

    Redirect mode serves ``<file-url>.metadata`` from a path we own, so the
    sidecar can be an ordinary static file. Each such wheel is downloaded once
    to a temporary file, read, and discarded — the one cost redirect mode pays
    over link mode.

    A truncated download needs no separate check: it fails the zip read, which
    is already handled and warned about. As in :func:`extract_metadata`, a
    failure warns and leaves the entry without metadata.
    """
    for files in projects.values():
        for entry in files:
            if not entry["filename"].endswith(".whl") or entry["metadata_api_url"]:
                continue
            if not entry["api_url"]:
                raise RedirectError(
                    f"{entry['filename']} has no asset API URL; "
                    "redirect mode cannot serve it"
                )
            if not entry["api_url"].startswith("https://"):
                # the request carries a bearer token; never send it in the clear
                raise RedirectError(
                    f"{entry['filename']}: refusing to fetch non-https URL: "
                    f"{entry['api_url']!r}"
                )
            asset_id = _asset_id(entry["api_url"])  # before spending a download
            request = urllib.request.Request(
                entry["api_url"],
                headers={
                    "Accept": "application/octet-stream",
                    "Authorization": f"Bearer {token}",
                },
            )
            # closed by the `with` below, before the file is read and before
            # the `finally` removes it — Windows refuses to unlink, and this
            # deliberately outlives the expression, hence delete=False
            wheel = tempfile.NamedTemporaryFile(  # noqa: SIM115
                suffix=".whl", delete=False
            )
            temp = Path(wheel.name)
            try:
                with (
                    wheel,
                    opener(  # nosec B310 — scheme validated above
                        request, timeout=60
                    ) as response,
                ):
                    for chunk in iter(lambda: response.read(65536), b""):
                        wheel.write(chunk)
                payload = read_wheel_metadata(temp)
            except (OSError, zipfile.BadZipFile) as error:
                print(
                    f"warning: cannot extract metadata from "
                    f"{entry['filename']}: {error}",
                    file=sys.stderr,
                )
                entry["core_metadata"] = False  # as extract_metadata does
                continue
            finally:
                temp.unlink(missing_ok=True)
            sidecar = out_dir / "_assets" / asset_id / f"{entry['filename']}.metadata"
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_bytes(payload)
            entry["core_metadata"] = hashlib.sha256(payload).hexdigest()


def metadata_coverage(projects: Projects) -> dict[str, tuple[int, int]]:
    """Per-source-repo ``(missing, total)`` counts of wheels lacking metadata."""
    coverage: dict[str, tuple[int, int]] = {}
    for files in projects.values():
        for entry in files:
            if not entry["filename"].endswith(".whl"):
                continue
            missing, total = coverage.get(entry["source_repo"], (0, 0))
            coverage[entry["source_repo"]] = (
                missing + (not entry["core_metadata"]),
                total + 1,
            )
    return dict(sorted(coverage.items()))


def pages_url(repo: str) -> str:
    """Return the GitHub Pages base URL for the ``owner/name`` repository."""
    owner, name = repo.split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}/"


def _sorted_versions(files: list[FileEntry]) -> list[str]:
    raw = {v for f in files if (v := version_from_filename(f["filename"]))}
    parseable: list[tuple[Version, str]] = []
    unparseable: list[str] = []
    for version in raw:
        try:
            parseable.append((Version(version), version))
        except InvalidVersion:
            unparseable.append(version)
    return [v for _, v in sorted(parseable)] + sorted(unparseable)


def _json_project_page(project: str, files: list[FileEntry]) -> str:
    entries = []
    for file in files:
        entry: dict[str, Any] = {
            "filename": file["filename"],
            "url": file["url"],
            "hashes": {"sha256": file["sha256"]} if file["sha256"] else {},
            "size": file["size"],
        }
        if file["upload_time"]:
            entry["upload-time"] = file["upload_time"]
        if file["core_metadata"]:
            core: dict[str, str] | bool = (
                {"sha256": file["core_metadata"]}
                if isinstance(file["core_metadata"], str)
                else True
            )
            entry["core-metadata"] = core
            entry["dist-info-metadata"] = core
        if file["yanked"]:
            entry["yanked"] = file["yanked"]
        entries.append(entry)
    return (
        json.dumps(
            {
                "meta": {"api-version": "1.1"},
                "name": project,
                "versions": _sorted_versions(files),
                "files": entries,
            },
            sort_keys=True,
        )
        + "\n"
    )


def _json_root(projects: Projects) -> str:
    return (
        json.dumps(
            {
                "meta": {"api-version": "1.1"},
                "projects": [{"name": name} for name in projects],
            },
            sort_keys=True,
        )
        + "\n"
    )


def write_site(
    projects: Projects,
    out_dir: Path,
    *,
    title: str,
    index_url: str | None,
    templates_dir: Path | None = None,
    formats: tuple[Formats, ...] = ("html", "json"),
) -> None:
    """Write the landing page and PEP 503/691 simple index under ``out_dir``.

    ``formats`` selects the HTML tree, the JSON tree (api-version 1.1), or
    both; the landing page is written only when ``html`` is included.
    """
    env = build_env(templates_dir) if "html" in formats else None
    simple = out_dir / "simple"
    simple.mkdir(parents=True, exist_ok=True)
    project_page = env.get_template("project.html") if env else None
    for project, files in projects.items():
        project_dir = simple / project
        project_dir.mkdir(parents=True, exist_ok=True)
        if project_page:
            (project_dir / "index.html").write_text(
                project_page.render(project=project, files=files), encoding="utf-8"
            )
        if "json" in formats:
            (project_dir / "index.json").write_text(
                _json_project_page(project, files), encoding="utf-8"
            )
    if "json" in formats:
        (simple / "index.json").write_text(_json_root(projects), encoding="utf-8")
    if env:
        (simple / "index.html").write_text(
            env.get_template("simple_root.html").render(projects=projects),
            encoding="utf-8",
        )
        (out_dir / "index.html").write_text(
            env.get_template("landing.html").render(
                title=title, index_url=index_url, projects=projects
            ),
            encoding="utf-8",
        )
