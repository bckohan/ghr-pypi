"""Typer command line interface for ghr-pypi."""

import os
import re
import urllib.error
import zipfile
from collections.abc import Iterable
from dataclasses import replace
from fnmatch import fnmatchcase
from importlib import resources
from pathlib import Path
from typing import Annotated

import typer

from ghr_pypi import index
from ghr_pypi.config import (
    Config,
    ConfigError,
    check_repository,
    check_slug,
    is_pattern,
    load,
)
from ghr_pypi.targets import SiteContext, available_targets, get_target

app = typer.Typer(add_completion=False, no_args_is_help=True)


def _resolve_config(
    repos: list[str] | None,
    config_path: Path | None,
    *,
    mirror: bool,
    env_repo: str | None,
    target: str | None = None,
) -> Config:
    """Resolve the build configuration from the command line and environment.

    ``env_repo`` is ``$GITHUB_REPOSITORY``; an empty value counts as unset. It
    is the last fallback in both branches and never conflicts with anything the
    user typed: GitHub Actions sets it for every step, so treating it as a
    conflict would break every config file user in CI. It is checked eagerly
    whenever it is set, before any network request, so a broken environment
    fails fast rather than at whichever later point first reads it — even in
    the cases where nothing ends up reading it at all.
    """
    env_repo = env_repo or None
    if env_repo is not None:
        check_slug(env_repo, "GITHUB_REPOSITORY")
        if is_pattern(env_repo):
            raise ConfigError(f"GITHUB_REPOSITORY {env_repo!r} may not be a pattern")
    repos = list(repos or [])
    if config_path is not None:
        if repos:
            raise ConfigError("with --config, list repositories in the config file")
        if mirror:
            raise ConfigError("with --config, set 'assets: mirror' in the config file")
        if target is not None:
            raise ConfigError("with --config, set 'target' in the config file")
        cfg = load(config_path)
        repositories = cfg.repositories
        if not repositories:
            if env_repo is None:
                raise ConfigError(
                    f"{config_path} has no 'repositories' and "
                    "GITHUB_REPOSITORY is not set"
                )
            repositories = (env_repo,)
    else:
        seen: set[str] = set()
        for repo in repos:
            check_repository(repo, "repository")
            if repo.casefold() in seen:
                raise ConfigError(f"repository {repo!r} given more than once")
            seen.add(repo.casefold())
        if repos:
            repositories = tuple(repos)
        elif env_repo is not None:
            repositories = (env_repo,)
        else:
            raise ConfigError("provide REPO..., set GITHUB_REPOSITORY, or use --config")
        cfg = Config(
            repositories=repositories,
            title=(
                f"{repositories[0]} package index"
                if len(repositories) == 1
                else "Package index"
            ),
            assets="mirror" if mirror else "link",
            target=target or "static",
        )
    url = cfg.url
    if url is None:
        if env_repo is not None:
            url = index.pages_url(env_repo)
        elif config_path is None and len(repositories) == 1:
            # Command line form only. A config that omits `url` has always
            # meant "no install example", and a repository listed there is not
            # necessarily the one serving the site.
            url = index.pages_url(repositories[0])
    return replace(cfg, repositories=repositories, url=url)


def _expand_patterns(
    repositories: tuple[str, ...],
    exclude: tuple[str, ...],
    token: str,
) -> tuple[str, ...]:
    """Expand ``OWNER/PATTERN`` entries by listing each owner's repositories.

    Matches are sorted and spliced where their pattern stood, because duplicate
    filenames across repositories resolve first-occurrence-wins — order is
    behavior, not presentation. Exclusions apply to expansions only: a
    repository named explicitly is always indexed.

    Every comparison is on casefolded strings, including the per-owner listing
    cache: GitHub owner and repository names are case-insensitive, so
    ``Org/lib-*`` and ``org/app-*`` must share one listing rather than paginate
    the same organization twice.
    """
    listings: dict[str, list[str]] = {}
    resolved: list[str] = []
    seen: set[str] = set()

    def add(repository: str) -> bool:
        """Append ``repository`` unless already resolved; True when it was new."""
        if repository.casefold() in seen:
            return False
        seen.add(repository.casefold())
        resolved.append(repository)
        return True

    for entry in repositories:
        owner, name = entry.split("/")
        if not is_pattern(name):
            add(entry)
            continue
        key = owner.casefold()
        if key not in listings:
            try:
                listings[key] = index.fetch_repositories(owner, token)
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    raise ConfigError(
                        f"{owner!r} is not a visible organization or user"
                    ) from error
                raise
        matched = sorted(
            found
            for found in listings[key]
            if fnmatchcase(found.split("/", 1)[1].casefold(), name.casefold())
        )
        if not matched:
            raise ConfigError(f"no repositories matched {entry!r}")
        kept = [
            found
            for found in matched
            if not any(
                fnmatchcase(found.casefold(), pattern.casefold()) for pattern in exclude
            )
        ]
        if not kept:
            raise ConfigError(
                f"every repository matching {entry!r} is excluded by "
                "exclude_repositories"
            )
        # count what this pattern actually contributes: reporting the match
        # count would overstate it when an earlier pattern already claimed some
        added = 0
        for found in kept:
            added += add(found)
        typer.echo(f"expanded {entry!r} to {added} repositories", err=True)
    return tuple(resolved)


def _warn_missing_metadata(projects: index.Projects) -> None:
    """Warn per repository about wheels the index can advertise no metadata for.

    Link mode can never close this gap — the sidecar would have to live at
    GitHub's ``<url>.metadata``, which we cannot write — and redirect mode
    under ``missing_metadata: warn`` has chosen not to. Same gap, same warning.
    """
    for repo_name, (missing, total) in index.metadata_coverage(projects).items():
        if missing:
            typer.echo(
                f"warning: {repo_name}: {missing} of {total} wheels have no "
                ".metadata asset; resolvers must download full wheels for "
                "dependency metadata",
                err=True,
            )


@app.command("index")
def build_index(
    repos: Annotated[
        list[str] | None,
        typer.Argument(
            metavar="[REPO]...",
            help="GitHub repositories as OWNER/NAME; defaults to "
            "$GITHUB_REPOSITORY (omit when using --config)",
        ),
    ] = None,
    out: Annotated[Path, typer.Option(help="Directory to write the index to")] = Path(
        "_site"
    ),
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            help=(
                "YAML config aggregating multiple repositories "
                "(list the repositories in it, not on the command line)"
            ),
        ),
    ] = None,
    token: Annotated[
        str | None,
        typer.Option(envvar="GITHUB_TOKEN", help="GitHub API token"),
    ] = None,
    mirror: Annotated[
        bool,
        typer.Option(
            "--mirror",
            help="Download assets into the site instead of linking to GitHub; "
            "shorthand for 'assets: mirror' (with --config, set that in the "
            "config file instead)",
        ),
    ] = False,
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
            help="Directory for operator artifacts that must NOT be published "
            "(allowed with --config, like --out)",
        ),
    ] = Path("."),
) -> None:
    """Build a PEP 503 package index from GitHub release assets."""
    if not token:
        typer.echo("error: provide --token or set GITHUB_TOKEN", err=True)
        raise typer.Exit(1)
    try:
        cfg = _resolve_config(
            repos,
            config,
            mirror=mirror,
            env_repo=os.environ.get("GITHUB_REPOSITORY"),
            target=target,
        )
    except ConfigError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from error
    # Resolve the target and claim its output directory before the first
    # network request. Both failures are knowable from the arguments alone, so
    # discovering either one after downloading every asset and writing the
    # whole site would be a waste the user cannot avoid.
    try:
        selected = get_target(cfg.target)
    except ValueError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from error
    if cfg.assets == "redirect" and not getattr(selected, "supports_redirect", False):
        # optional attribute, read defensively: a target written before the
        # mode existed simply cannot serve it, and must not have to declare so
        qualified = sorted(
            name
            for name, candidate in available_targets().items()
            if getattr(candidate, "supports_redirect", False)
        )
        typer.echo(
            f"error: target {cfg.target!r} cannot serve 'assets: redirect'; "
            f"targets that can: {', '.join(qualified)}",
            err=True,
        )
        raise typer.Exit(1)
    try:
        target_out.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        typer.echo(f"error: cannot create --target-out {target_out}: {error}", err=True)
        raise typer.Exit(1) from error
    try:
        cfg = replace(
            cfg,
            repositories=_expand_patterns(
                cfg.repositories, cfg.exclude_repositories, token
            ),
        )
    except ConfigError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from error
    except (urllib.error.URLError, index.PaginationError) as error:
        typer.echo(f"error: listing repositories failed: {error}", err=True)
        raise typer.Exit(1) from error
    releases = []
    try:
        for current in cfg.repositories:
            fetched = index.fetch_releases(current, token)
            for release in fetched:
                # fetched payloads are fresh json.load output; safe to tag in place
                release["_source_repo"] = current
            releases.extend(fetched)
    except (urllib.error.URLError, index.PaginationError) as error:
        typer.echo(f"error: GitHub API request for {current} failed: {error}", err=True)
        raise typer.Exit(1) from error
    try:
        # pass via module attribute so tests can monkeypatch index.hash_url
        projects = index.collect_projects(
            releases,
            hash_url=index.hash_url,
            missing_digest=cfg.missing_digest,
            defer_hash=cfg.assets == "mirror",
            metadata=cfg.metadata,
            filters=cfg.filters,
        )
    except urllib.error.URLError as error:
        typer.echo(f"error: downloading a release asset failed: {error}", err=True)
        raise typer.Exit(1) from error
    if not projects:
        typer.echo(
            f"error: no package assets found in releases of "
            f"{', '.join(cfg.repositories)}; refusing to build an empty index",
            err=True,
        )
        raise typer.Exit(1)
    if cfg.assets == "mirror":
        try:
            index.mirror_files(projects, out, token)
        except index.MirrorError as error:
            typer.echo(f"error: {error}", err=True)
            raise typer.Exit(1) from error
        except urllib.error.URLError as error:
            typer.echo(f"error: downloading a release asset failed: {error}", err=True)
            raise typer.Exit(1) from error
        if cfg.metadata:
            index.extract_metadata(projects, out)
    elif cfg.assets == "redirect":
        try:
            # redirect_urls first: it rejects a malformed or missing api_url,
            # so a bad entry fails before any wheel is downloaded. It does not
            # modify api_url, so the two calls after it still see what they
            # need — do not "optimise" this order back.
            index.redirect_urls(projects)
            if cfg.metadata and cfg.missing_metadata == "extract":
                index.extract_missing_metadata(projects, out, token)
            index.write_manifest(projects, out)
        except index.RedirectError as error:
            typer.echo(f"error: {error}", err=True)
            raise typer.Exit(1) from error
        except urllib.error.URLError as error:
            typer.echo(f"error: downloading a release asset failed: {error}", err=True)
            raise typer.Exit(1) from error
        if cfg.metadata and cfg.missing_metadata == "warn":
            # 'warn' buys back the downloads by giving up the sidecars, which
            # leaves exactly the gap link mode has — so it reports it the same way
            _warn_missing_metadata(projects)
    elif cfg.metadata:
        _warn_missing_metadata(projects)
    index_url = cfg.url.rstrip("/") + "/simple/" if cfg.url else None
    index.write_site(
        projects,
        out,
        title=cfg.title,
        index_url=index_url,
        templates_dir=cfg.templates,
        formats=cfg.formats,
    )
    # after write_site, which is what creates out_dir: the target runs against
    # the finished site, so it can read the tree it is describing
    try:
        result = selected.emit(
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
        if isinstance(result, (str, bytes)) or not isinstance(result, Iterable):
            # a target that returns None — the shape a plugin author gets by
            # forgetting the return — would otherwise blow up as a bare
            # TypeError in the echo loop, after the success line was printed
            typer.echo(
                f"error: {cfg.target} target returned {type(result).__name__}, "
                "expected a sequence of paths",
                err=True,
            )
            raise typer.Exit(1)
        # materialize inside this guard rather than iterating lazily below:
        # emit is annotated Sequence, but a plugin that ignores that and
        # yields as it writes would otherwise defer every write past this
        # except clause and fail after the build reported success
        written = list(result)
    except OSError as error:
        typer.echo(f"error: {cfg.target} target failed: {error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(f"wrote index for {len(projects)} project(s) to {out}")
    # naming each artifact is what keeps one landing outside the published
    # tree — --target-out defaults to the working directory — from being a
    # surprise the operator only finds at commit time
    for path in written:
        typer.echo(f"wrote {path} for the {cfg.target} target")


@app.command("extract-meta")
def extract_meta(
    paths: Annotated[
        list[Path],
        typer.Argument(
            metavar="PATH...",
            help="Wheels, or directories to scan (non-recursively) for *.whl",
        ),
    ],
) -> None:
    """Write each wheel's PEP 658 core metadata to <wheel>.metadata.

    Upload the sidecars alongside their wheels in the same release: the index
    can only advertise metadata that lives at the wheel's own URL plus
    .metadata.
    """
    wheels: list[Path] = []
    for path in paths:
        if not path.exists():
            typer.echo(f"error: {path} does not exist", err=True)
            raise typer.Exit(1)
        if path.is_dir():
            found = sorted(path.glob("*.whl"))
            if not found:
                typer.echo(f"error: no wheels in {path}", err=True)
                raise typer.Exit(1)
            wheels.extend(found)
        elif path.suffix == ".whl":
            wheels.append(path)
        else:
            typer.echo(f"error: {path} is not a wheel", err=True)
            raise typer.Exit(1)
    wheels = list(dict.fromkeys(wheels))
    # read everything before writing anything: either every sidecar lands or
    # none does, so a failure never leaves a release half-annotated
    payloads: list[tuple[Path, bytes]] = []
    for wheel in wheels:
        try:
            payloads.append(
                (index.metadata_path(wheel), index.read_wheel_metadata(wheel))
            )
        except (OSError, zipfile.BadZipFile) as error:
            typer.echo(
                f"error: cannot extract metadata from {wheel}: {error}", err=True
            )
            raise typer.Exit(1) from error
    for target, payload in payloads:
        try:
            target.write_bytes(payload)
        except OSError as error:
            typer.echo(f"error: cannot write {target}: {error}", err=True)
            raise typer.Exit(1) from error
        typer.echo(f"wrote {target}")
    typer.echo(f"extracted metadata from {len(wheels)} wheel(s)")


_WEBHOOK_WORKER = "webhook_worker.js"

# The Worker's name, and therefore the host in its workers.dev URL. Shared by
# the manifest and the checklist so the payload URL the operator is told to
# paste into GitHub is the one the deploy actually creates.
_WEBHOOK_NAME = "ghr-pypi-webhook"

# Pinned rather than "today", for the reason the cloudflare target pins its
# own: a compatibility date states which runtime semantics the Worker was
# tested against, so regenerating the bundle must not silently move it.
_WEBHOOK_COMPATIBILITY_DATE = "2026-08-09"

# The shape webhook_worker.js enforces on INDEX_REPO at runtime, restated here
# so a repository it would refuse — with a 500 on every delivery, long after
# the deploy looked fine — is refused while it is still a typo on a command
# line. It is also what keeps the interpolation below honest: check_slug alone
# admits quotes and newlines, which would rewrite the generated wrangler.toml.
_WEBHOOK_REPO = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_WEBHOOK_DOT_SEGMENT = re.compile(r"(^|/)\.+($|/)")

_WEBHOOK_WRANGLER = """\
# Generated by ghr-pypi. REGENERATED ON EVERY RUN — re-apply any edit.
name = "{name}"
main = "worker.js"
compatibility_date = "{compatibility_date}"

[vars]
INDEX_REPO = "{index_repo}"
"""

# A checklist, not an explanation: every command below is `wrangler ...` and
# never `wrangler pages ...`, because this is a standalone Worker. The Pages
# family would bind the secrets to a Pages project instead, and the receiver
# would answer every delivery with "Receiver is not configured".
_WEBHOOK_SETUP = """\
# Deploying the ghr-pypi webhook receiver

This Worker turns a release published in *any* repository the webhook covers
into a `repository_dispatch` that rebuilds the index in
`{index_repo}`. Without it, only a release in that repository rebuilds it.

Generated by ghr-pypi and **regenerated on every run**, along with the
`wrangler.toml` beside it — edits to either are lost.

Run everything below from the directory holding this file: wrangler discovers
`wrangler.toml` by walking up from the working directory, and that file is
where the Worker's name and `INDEX_REPO` come from.

Every command is a plain `wrangler ...`. This is a standalone Worker, not a
Pages project, so the `wrangler pages ...` family would configure something
else and leave this receiver unconfigured.

## 1. Deploy

```
wrangler deploy
```

This publishes the Worker and prints its URL, of the form
`https://{name}.<your-subdomain>.workers.dev` —
the payload URL step 3 asks for.

Deploy first, unlike a Pages project, where the secrets have to be bound before
the deployment that reads them. A Worker is the other way round: `wrangler
secret put` publishes a new version immediately, so a secret bound in step 2
needs no redeploy — while running step 2 first would find no Worker of this
name and offer to create a placeholder one instead, which a non-interactive
shell accepts silently. Between the two steps the receiver answers every
request with `500 Receiver is not configured`, which is safe: it is failing
closed, and nothing is pointed at it until step 3.

## 2. Bind both secrets

```
wrangler secret put WEBHOOK_SECRET
wrangler secret put GITHUB_TOKEN
```

Both are required, and neither ever leaves the Worker. `WEBHOOK_SECRET` is any
high-entropy string — keep it, because step 3 asks GitHub for the same value.
`GITHUB_TOKEN` needs `contents: write` and nothing else on
`{index_repo}` — on a fine-grained token, the *Contents* permission at
*Read and write*. That is what `POST /repos/OWNER/NAME/dispatches` requires.

Rotating either one later is this step again, with no redeploy.

## 3. Create the webhook

In the organization (or repository) settings, under Webhooks → Add webhook:

- **Payload URL** — the URL step 1 printed.
- **Content type** — `application/json`.
- **Secret** — the exact `WEBHOOK_SECRET` value from step 2. A mismatch is a
  401 on every delivery, visible in the hook's Recent Deliveries.
- **Which events** — "Let me select individual events", then **Releases**
  only. Everything else is signature-checked and dropped with a 204, so
  sending more only burns invocations.

The two scopes need different standing, and the organization one is the
stricter: GitHub requires an organization **owner** to create an organization
webhook, while a repository webhook needs only repository ownership or admin
access on that repository. One organization webhook covers every repository the
organization owns, which is the point of this receiver; per-repository hooks are
the fallback when you administer the repositories but do not own the
organization.

GitHub sends a `ping` on creation; the receiver answers `200 pong`, so a green
first delivery means the secret matches.

## 4. Make the index repository listen

The index repository —
`{index_repo}` — must have a workflow triggered by `repository_dispatch` on the
type `ghr-pypi-rebuild`, which is the event type this Worker sends. Until it
does, every hop still reports success and nothing rebuilds: GitHub's dispatch
endpoint answers this Worker `204 No Content`, and this Worker answers GitHub
`202`, a green delivery. Neither number says whether a workflow was listening.
"""


@app.command("webhook")
def webhook(
    index_repo: Annotated[
        str,
        typer.Option(
            "--index-repo",
            help="Repository whose Pages workflow rebuilds the index, as OWNER/NAME",
        ),
    ],
    out: Annotated[
        Path,
        typer.Option("--out", help="Directory to write the receiver into"),
    ] = Path("webhook"),
) -> None:
    """Write a Cloudflare Worker that rebuilds the index on a release elsewhere.

    A release in the index's own repository already triggers its Pages
    workflow; a release anywhere else does not, so an aggregating index goes
    stale. Deploy this receiver and point an organization webhook at it.

    Use this only where you have the standing to create the webhook: an
    organization webhook requires an organization owner, a repository webhook
    ownership or admin access on that repository. SETUP.md, written beside the
    Worker, is the deployment checklist.
    """
    # validate before creating anything: a rejected --index-repo must not leave
    # an empty directory behind for the operator to wonder about
    try:
        check_slug(index_repo, "--index-repo")
    except ConfigError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from error
    if is_pattern(index_repo):
        typer.echo(
            f"error: --index-repo {index_repo!r} must name one repository, "
            "not a pattern",
            err=True,
        )
        raise typer.Exit(1)
    if (
        not _WEBHOOK_REPO.match(index_repo)
        or ".." in index_repo
        or _WEBHOOK_DOT_SEGMENT.search(index_repo)
    ):
        typer.echo(
            f"error: --index-repo {index_repo!r} is not a GitHub repository name; "
            "use letters, digits, '.', '_' and '-' either side of the slash",
            err=True,
        )
        raise typer.Exit(1)
    worker = (
        resources.files("ghr_pypi")
        .joinpath(_WEBHOOK_WORKER)
        .read_text(encoding="utf-8")
    )
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        typer.echo(f"error: cannot create --out {out}: {error}", err=True)
        raise typer.Exit(1) from error
    for name, content in (
        ("worker.js", worker),
        (
            "wrangler.toml",
            _WEBHOOK_WRANGLER.format(
                name=_WEBHOOK_NAME,
                compatibility_date=_WEBHOOK_COMPATIBILITY_DATE,
                index_repo=index_repo,
            ),
        ),
        ("SETUP.md", _WEBHOOK_SETUP.format(name=_WEBHOOK_NAME, index_repo=index_repo)),
    ):
        target = out / name
        try:
            target.write_text(content, encoding="utf-8")
        except OSError as error:
            typer.echo(f"error: cannot write {target}: {error}", err=True)
            raise typer.Exit(1) from error
        typer.echo(f"wrote {target}")
    # the three lines above name files; this one names the next move, which for
    # a bundle that nothing deploys on its own is the part an operator misses
    typer.echo(f"wrote a webhook receiver for {index_repo} to {out}; see SETUP.md")
