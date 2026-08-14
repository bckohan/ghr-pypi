"""Pluggable emitters for host-specific deployment artifacts.

Each target writes what one host needs beside the index and nothing else. What
URLs the index contains is decided by the ``assets`` mode, not by the target,
which is why a target never rewrites a link and why the same index can be
deployed anywhere.

The rest — the built-ins, the entry point, the plugin contract — lives in the
manual's "Deployment targets" reference rather than being restated here; the
docstrings below are what that page renders.

Import direction is one-way: this package imports ``config`` and ``index``,
and neither imports it. Keep it that way.
"""

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import Protocol, runtime_checkable

from ghr_pypi.config import AssetMode, AuthMode, Formats
from ghr_pypi.index import Projects

ENTRY_POINT_GROUP = "ghr_pypi.targets"


@dataclass(frozen=True)
class SiteContext:
    """Everything a target may read about a build.

    ``out_dir`` is published; ``target_dir`` is not. An artifact that must
    never be served — a server snippet, a deploy script — belongs in
    ``target_dir``: writing it under ``out_dir`` would publish it to anyone
    who can reach the index.

    A target must treat ``projects`` as read-only. Freezing the dataclass
    stops the field being rebound, not the mapping being mutated in place, so
    the guarantee is by contract rather than enforced: a target that edited it
    would silently change what every later target — and the already-written
    index — describes.
    """

    projects: Projects
    """Every indexed project, mapped to the files the index describes for it.

    Read-only by contract. The keys are :pep:`503`-normalized project names,
    the same names that appear under ``simple/``; each value is a list of
    :class:`~ghr_pypi.index.FileEntry`.
    """

    out_dir: Path
    """The finished site — the value of ``--out``. Everything under it is
    published, so only write here what the *host* reads out of the deployed
    tree."""

    target_dir: Path
    """Operator-side output — the value of ``--target-out``, defaulting to the
    working directory. Nothing here is published or copied into ``out_dir``."""

    title: str
    """The landing page heading, from ``title`` or derived from the single
    repository being indexed. Useful for naming a generated artifact after the
    index it belongs to."""

    index_url: str | None
    """The absolute URL of the simple index — ``url`` with trailing slashes
    stripped and ``/simple/`` appended — or None when nothing supplied a
    ``url``. A target needing an absolute address must handle the None."""

    assets: AssetMode
    """Where the index's links point — ``"link"``, ``"mirror"`` or
    ``"redirect"``. A target reads it to vary what it emits: there is no
    ``files/`` directory to describe under ``"link"``, and ``"redirect"`` points
    every link at the site's own ``_assets/`` paths, which something on the host
    must be set up to serve — which is why a target only ever sees this value
    if it declares ``supports_redirect``."""

    formats: tuple[Formats, ...]
    """Which representations were written — ``"html"``, ``"json"``, or both, in
    the order given. A rule about ``index.json`` is pointless when ``"json"``
    is absent."""

    auth: AuthMode = "basic"
    """How the served index authenticates clients — ``"basic"`` (shared
    credentials) or ``"github"`` (each client's Basic-auth password is their
    own GitHub token). Defaulted so a target built before the field existed
    keeps constructing; only a target declaring ``supports_github_auth`` ever
    sees ``"github"``."""

    gate_repository: str | None = None
    """Under ``auth: github``, the ``OWNER/NAME`` whose readability gates the
    index pages — already validated and defaulted by the CLI, so a target may
    interpolate it. None whenever ``auth`` is ``"basic"``."""


@runtime_checkable
class Target(Protocol):
    """Writes one host's deployment artifacts.

    A target may also declare ``supports_redirect = True`` to accept
    ``assets: redirect``, whose links point at the site's own ``_assets/``
    paths rather than at a URL any client can fetch, and
    ``supports_github_auth = True`` to accept ``auth: github``, whose serving
    path must validate a client-supplied GitHub token. Neither is a member of
    this protocol, deliberately: the CLI reads both with
    ``getattr(target, ..., False)``, so every target written before either
    mode existed keeps satisfying ``Target`` and is simply refused that one
    mode, with an error naming the targets that qualify.
    """

    name: str

    def emit(self, site: SiteContext) -> Sequence[Path]:
        """Write this target's artifacts for ``site`` and return their paths.

        Return every path written, or an empty sequence when there is nothing
        to write. The CLI reports these to the operator, which is the only
        thing that makes an artifact landing outside the published tree — a
        server snippet dropped into the working directory — visible rather
        than a surprise at commit time.

        A ``Sequence``, deliberately not an ``Iterable``: every write must
        have happened by the time this returns. A generator that yielded each
        path as it wrote it would instead defer the writes to whenever the
        caller consumed them, which puts the moment a write can fail outside
        the target's own control. A type checker rejects that against this
        annotation, which is where it should be caught.

        The CLI is defensive about it anyway — it materializes whatever it
        gets inside the error handling around this call, so a generator that
        slipped past type checking still fails cleanly there rather than
        escaping. That is the CLI's belt and braces, not a promise of the
        protocol: another caller need not be so careful, which is why the
        contract is a materialized sequence.
        """
        ...


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

    A plugin that raises on load — a stale entry point, a value that is not
    callable — is skipped with a warning rather than propagated, for the same
    reason: an unreferenced plugin must not be able to fail a build that only
    ever asked for a built-in target.
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
        try:
            registry[entry.name] = entry.load()()
        except Exception as error:  # noqa: BLE001 — one bad plugin must not
            # break every build, including the built-in targets the user
            # actually asked for. Naming both the entry point and its value is
            # what makes an unreferenced, transitively installed plugin
            # traceable.
            print(
                f"warning: ignoring plugin target {entry.name!r} from "
                f"{entry.value}: {error}",
                file=sys.stderr,
            )
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
