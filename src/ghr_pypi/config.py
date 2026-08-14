"""Load and validate the YAML configuration for multi-repository indexes."""

import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, cast

import yaml
from packaging.version import InvalidVersion, Version

MissingDigest = Literal["download", "no-fragment", "omit"]

Formats = Literal["html", "json"]

AssetMode = Literal["link", "mirror", "redirect"]

MissingMetadata = Literal["extract", "warn"]

AuthMode = Literal["basic", "github"]

_KNOWN_KEYS = {
    "repositories",
    "templates",
    "title",
    "url",
    "missing_digest",
    "formats",
    "assets",
    "mirror",  # deprecated alias for 'assets'; remove no earlier than the 2027.1 release
    "metadata",
    "missing_metadata",
    "yanked",
    "exclude",
    "exclude_repositories",
    "target",
    "auth",
    "gate_repository",
}

# A whole segment of nothing but dots walks a URL path, and no real owner or
# repo is named "." or "..". Same rule the webhook receiver applies to
# INDEX_REPO, for the same reason: the gate is interpolated into generated
# JavaScript and into a GitHub API path.
_DOT_SEGMENT = re.compile(r"(^|/)\.+($|/)")


class ConfigError(ValueError):
    """Raised when the build configuration is missing or invalid.

    Covers the YAML file as well as the command line arguments and environment
    that ``cli._resolve_config`` folds into a :class:`Config`.
    """


def check_slug(value: Any, label: str) -> None:
    """Raise ``ConfigError`` unless ``value`` is an ``OWNER/NAME`` slug.

    ``label`` names the source of the value ("repository",
    "GITHUB_REPOSITORY"); the value's repr is appended by this function, so
    callers never format it themselves.
    """
    parts = value.split("/") if isinstance(value, str) else []
    if len(parts) != 2 or not all(parts):
        raise ConfigError(f"{label} {value!r} is not OWNER/NAME")


_PATTERN_CHARS = "*?["


def is_pattern(name: str) -> bool:
    """Return True when a repository name half is an ``fnmatch`` pattern.

    The character set is exactly what ``fnmatch`` treats as special — testing
    only for ``*`` and ``?`` would silently read ``yourorg/lib-[ab]`` as a
    literal repository name.
    """
    return any(char in name for char in _PATTERN_CHARS)


def check_repository(value: Any, label: str) -> None:
    """Raise ``ConfigError`` unless ``value`` is ``OWNER/NAME`` or ``OWNER/PATTERN``.

    The owner half may never be a pattern: there is no GitHub endpoint for
    "every organization I can see".
    """
    check_slug(value, label)
    if is_pattern(value.split("/")[0]):
        raise ConfigError(f"{label} {value!r} may not use a pattern in the owner")


def _normalize(name: str) -> str:
    """Normalize a project name per PEP 503.

    A copy of ``index.normalize``: ``config`` cannot import ``index`` because
    ``index`` imports this module.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def _same_version(configured: str, found: str) -> bool:
    """Return True when two version strings denote the same release.

    Compares as :class:`packaging.version.Version` so ``1.0`` matches
    ``1.0.0``, falling back to exact string equality when either side is not a
    valid version.
    """
    if configured == found:
        return True
    try:
        return Version(configured) == Version(found)
    except InvalidVersion:
        return False


@dataclass(frozen=True)
class Filters:
    """Per-project, per-version yank and exclusion rules.

    ``yanked`` maps a project to a mapping of version to PEP 592 reason (a
    string, or True for "yanked, no reason given"). ``exclude`` maps a project
    to the versions to keep out of the index entirely. Project keys are
    PEP 503-normalized and both mappings are made read-only on construction;
    keys that normalize to the same project raise ``ValueError`` rather than
    silently discarding one of them.
    """

    yanked: Mapping[str, Mapping[str, str | bool]] = field(default_factory=dict)
    exclude: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        yanked: dict[str, Mapping[str, str | bool]] = {}
        for project, reasons in self.yanked.items():
            if (normalized := _normalize(project)) in yanked:
                raise ValueError(
                    f"Filters.yanked has two entries for project {normalized!r}"
                )
            yanked[normalized] = MappingProxyType(dict(reasons))
        exclude: dict[str, tuple[str, ...]] = {}
        for project, versions in self.exclude.items():
            if (normalized := _normalize(project)) in exclude:
                raise ValueError(
                    f"Filters.exclude has two entries for project {normalized!r}"
                )
            exclude[normalized] = tuple(versions)
        object.__setattr__(self, "yanked", MappingProxyType(yanked))
        object.__setattr__(self, "exclude", MappingProxyType(exclude))

    def yank_reason(self, project: str, version: str | None) -> str | bool:
        """Return the yank reason for ``project`` ``version``, else False.

        The reason is the configured string, or True when the version is
        yanked without one. A ``version`` of None (unparseable filename)
        matches nothing.

        Callers test the result for truth, so a falsy reason reads as "not
        yanked": :func:`load` rejects an empty string, but ``Filters`` is
        directly constructible and does not.
        """
        if version is None:
            return False
        for configured, reason in self.yanked.get(_normalize(project), {}).items():
            if _same_version(configured, version):
                return reason
        return False

    def is_excluded(self, project: str, version: str | None) -> bool:
        """Return whether ``project`` ``version`` is excluded from the index.

        A ``version`` of None (unparseable filename) matches nothing.
        """
        if version is None:
            return False
        return any(
            _same_version(configured, version)
            for configured in self.exclude.get(_normalize(project), ())
        )


NO_FILTERS = Filters()
"""The empty :class:`Filters` — yanks nothing, excludes nothing.

Deeply immutable, so it is safe to share as a default argument.
"""


@dataclass(frozen=True)
class Config:
    """Validated build configuration.

    An empty ``repositories`` means the key was not configured, not that the
    index should contain nothing; the caller must supply the repositories
    from elsewhere.
    """

    repositories: tuple[str, ...] = ()
    templates: Path | None = None
    title: str = "Package index"
    url: str | None = None
    missing_digest: MissingDigest = "download"
    formats: tuple[Formats, ...] = ("html", "json")
    assets: AssetMode = "link"
    metadata: bool = True
    missing_metadata: MissingMetadata = "extract"
    filters: Filters = NO_FILTERS
    exclude_repositories: tuple[str, ...] = ()
    target: str = "static"
    """Name of the deployment target that emits host artifacts.

    Validated as a string only. Resolving the name against the target registry
    is the CLI's job: ``targets`` imports ``index``, which imports this module,
    so looking it up here would close an import cycle.
    """
    auth: AuthMode = "basic"
    """How the served index authenticates clients.

    ``basic`` is the shared-credential mode every deployment used before this
    key existed. ``github`` makes each client's Basic-auth password their own
    GitHub token, requires ``assets: redirect``, and is only accepted by a
    target declaring ``supports_github_auth`` — which, like ``target`` itself,
    is enforced by the CLI to keep the import direction one-way.
    """
    gate_repository: str | None = None
    """The ``OWNER/NAME`` whose readability gates index pages under
    ``auth: github``, or None to default to ``$GITHUB_REPOSITORY`` — the
    repository the workflow runs in, which for a template deployment is the
    index repository itself. The CLI resolves the default; a None reaching a
    target under ``auth: github`` is a bug."""


def _yanked(path: Path, raw: Any) -> dict[str, dict[str, str | bool]]:
    """Validate the ``yanked`` key's value, keyed by normalized project name."""
    if not isinstance(raw, dict):
        raise ConfigError(
            f"{path}: 'yanked' must be a mapping of project name to "
            "a mapping of version to reason"
        )
    yanked: dict[str, dict[str, str | bool]] = {}
    for project, reasons in raw.items():
        if not isinstance(project, str):
            raise ConfigError(
                f"{path}: 'yanked' project keys must be strings, got {project!r}"
            )
        if not isinstance(reasons, dict):
            raise ConfigError(
                f"{path}: 'yanked.{project}' must be a mapping of version to reason"
            )
        seen: list[str] = []
        for version, reason in reasons.items():
            if not isinstance(version, str):
                raise ConfigError(
                    f"{path}: 'yanked.{project}' version keys must be quoted "
                    f'strings, got {version!r}; write it as "{version}"'
                )
            if reason is False:
                raise ConfigError(
                    f"{path}: 'yanked.{project}.{version}' is false; remove the "
                    "entry to un-yank the version"
                )
            if reason == "":
                raise ConfigError(
                    f"{path}: 'yanked.{project}.{version}' has an empty reason; "
                    "use true for a yank with no reason"
                )
            if reason is not True and not isinstance(reason, str):
                raise ConfigError(
                    f"{path}: 'yanked.{project}.{version}' must be a reason "
                    f"string or true, got {reason!r}"
                )
            if any(_same_version(version, prior) for prior in seen):
                raise ConfigError(
                    f"{path}: 'yanked.{project}' has two entries for version "
                    f"{version!r}"
                )
            seen.append(version)
        if (normalized := _normalize(project)) in yanked:
            raise ConfigError(
                f"{path}: 'yanked' has two entries for project {normalized!r}"
            )
        yanked[normalized] = dict(reasons)
    return yanked


def _exclude(path: Path, raw: Any) -> dict[str, tuple[str, ...]]:
    """Validate the ``exclude`` key's value, keyed by normalized project name."""
    if not isinstance(raw, dict):
        raise ConfigError(
            f"{path}: 'exclude' must be a mapping of project name to a list of versions"
        )
    exclude: dict[str, tuple[str, ...]] = {}
    for project, versions in raw.items():
        if not isinstance(project, str):
            raise ConfigError(
                f"{path}: 'exclude' project keys must be strings, got {project!r}"
            )
        if not isinstance(versions, list):
            raise ConfigError(
                f"{path}: 'exclude.{project}' must be a list of version strings"
            )
        seen: list[str] = []
        for version in versions:
            if not isinstance(version, str):
                raise ConfigError(
                    f"{path}: 'exclude.{project}' versions must be quoted "
                    f'strings, got {version!r}; write it as "{version}"'
                )
            if any(_same_version(version, prior) for prior in seen):
                raise ConfigError(
                    f"{path}: 'exclude.{project}' has two entries for version "
                    f"{version!r}"
                )
            seen.append(version)
        if (normalized := _normalize(project)) in exclude:
            raise ConfigError(
                f"{path}: 'exclude' has two entries for project {normalized!r}"
            )
        exclude[normalized] = tuple(versions)
    return exclude


def load(path: Path) -> Config:
    """Parse and validate the YAML config file at ``path``."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(f"cannot read config file {path}: {error}") from error
    except UnicodeDecodeError as error:
        raise ConfigError(f"config file {path} is not valid UTF-8: {error}") from error
    except yaml.YAMLError as error:
        raise ConfigError(f"invalid YAML in {path}: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    if unknown := set(raw) - _KNOWN_KEYS:
        raise ConfigError(f"{path}: unknown key(s): {', '.join(sorted(unknown))}")
    repositories = []
    # optional: callers may supply the repositories from elsewhere
    if (raw_repositories := raw.get("repositories")) is not None:
        if not isinstance(raw_repositories, list) or not raw_repositories:
            raise ConfigError(f"{path}: 'repositories' must be a non-empty list")
        for repo in raw_repositories:
            check_repository(repo, f"{path}: repository")
        if len({r.casefold() for r in raw_repositories}) != len(raw_repositories):
            raise ConfigError(f"{path}: 'repositories' contains duplicates")
        repositories = raw_repositories
    exclude_repositories: list[Any] = []
    if (raw_exclude := raw.get("exclude_repositories")) is not None:
        if not isinstance(raw_exclude, list):
            raise ConfigError(
                f"{path}: 'exclude_repositories' must be a list of patterns"
            )
        exclude_repositories = raw_exclude
    for pattern in exclude_repositories:
        check_repository(pattern, f"{path}: exclude_repositories entry")
    templates = None
    if (raw_templates := raw.get("templates")) is not None:
        if not isinstance(raw_templates, str):
            raise ConfigError(f"{path}: 'templates' must be a string path")
        templates = (path.parent / raw_templates).resolve()
        if not templates.is_dir():
            raise ConfigError(f"{path}: templates directory not found: {templates}")
    url = raw.get("url")
    if url is not None and not isinstance(url, str):
        raise ConfigError(f"{path}: 'url' must be a string")
    if url is not None and not url.startswith("https://"):
        raise ConfigError(f"{path}: 'url' must be https")
    title = raw.get("title", "Package index")
    if not isinstance(title, str):
        raise ConfigError(f"{path}: 'title' must be a string")
    missing_digest = raw.get("missing_digest", "download")
    if missing_digest not in ("download", "no-fragment", "omit"):
        raise ConfigError(
            f"{path}: 'missing_digest' must be one of "
            f"download, no-fragment, omit, got {missing_digest!r}"
        )
    raw_formats = raw.get("formats", ["html", "json"])
    if not isinstance(raw_formats, list) or not raw_formats:
        raise ConfigError(f"{path}: 'formats' must be a non-empty list")
    for fmt in raw_formats:
        if fmt not in ("html", "json"):
            raise ConfigError(
                f"{path}: 'formats' entries must be html or json, got {fmt!r}"
            )
    if len(set(raw_formats)) != len(raw_formats):
        raise ConfigError(f"{path}: 'formats' contains duplicates")
    formats = cast(tuple[Formats, ...], tuple(raw_formats))
    if "assets" in raw and "mirror" in raw:
        raise ConfigError(
            f"{path}: set either 'assets' or the deprecated 'mirror', not both"
        )
    if "mirror" in raw:
        raw_mirror = raw["mirror"]
        if not isinstance(raw_mirror, bool):
            raise ConfigError(f"{path}: 'mirror' must be true or false")
        assets: AssetMode = "mirror" if raw_mirror else "link"
        # print(..., file=sys.stderr), not warnings.warn(DeprecationWarning):
        # Python filters DeprecationWarning by default outside __main__, and
        # this fires inside config.load, so it would be invisible to exactly
        # the console-script users who need to see it. A stderr print is
        # both correct here and consistent with every other operator-facing
        # warning in this module.
        print(
            f"warning: {path}: 'mirror' is deprecated; write assets: {assets}",
            file=sys.stderr,
        )
    else:
        assets = raw.get("assets", "link")
        if assets not in ("link", "mirror", "redirect"):
            raise ConfigError(
                f"{path}: 'assets' must be one of link, mirror, redirect, "
                f"got {assets!r}"
            )
    if assets == "mirror" and "missing_digest" in raw:
        raise ConfigError(
            f"{path}: 'missing_digest' has no effect when 'assets' is mirror"
        )
    metadata = raw.get("metadata", True)
    if not isinstance(metadata, bool):
        raise ConfigError(f"{path}: 'metadata' must be true or false")
    missing_metadata = raw.get("missing_metadata", "extract")
    if missing_metadata not in ("extract", "warn"):
        raise ConfigError(
            f"{path}: 'missing_metadata' must be one of extract, warn, "
            f"got {missing_metadata!r}"
        )
    if "missing_metadata" in raw and assets != "redirect":
        raise ConfigError(
            f"{path}: 'missing_metadata' only applies when 'assets' is redirect; "
            "remove it, or set assets: redirect"
        )
    target = raw.get("target", "static")
    if not isinstance(target, str):
        raise ConfigError(f"{path}: 'target' must be a string")
    auth = raw.get("auth", "basic")
    if auth not in ("basic", "github"):
        raise ConfigError(f"{path}: 'auth' must be one of basic, github, got {auth!r}")
    if auth == "github" and assets != "redirect":
        raise ConfigError(
            f"{path}: 'auth: github' only applies when 'assets' is redirect; "
            "remove it, or set assets: redirect"
        )
    gate_repository = raw.get("gate_repository")
    if gate_repository is not None:
        if auth != "github":
            raise ConfigError(
                f"{path}: 'gate_repository' only applies when 'auth' is github"
            )
        check_slug(gate_repository, f"{path}: gate_repository")
        if _DOT_SEGMENT.search(gate_repository) or ".." in gate_repository:
            raise ConfigError(
                f"{path}: gate_repository {gate_repository!r} is not OWNER/NAME"
            )
    filters = Filters(
        yanked=_yanked(path, raw.get("yanked", {})),
        exclude=_exclude(path, raw.get("exclude", {})),
    )
    return Config(
        repositories=tuple(repositories),
        templates=templates,
        title=title,
        url=url,
        missing_digest=cast(MissingDigest, missing_digest),
        formats=formats,
        assets=assets,
        metadata=metadata,
        missing_metadata=missing_metadata,
        filters=filters,
        exclude_repositories=tuple(exclude_repositories),
        target=target,
        auth=cast(AuthMode, auth),
        gate_repository=gate_repository,
    )
