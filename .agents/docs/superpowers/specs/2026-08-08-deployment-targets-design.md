# Deployment Targets — Design

**Date:** 2026-08-08
**Status:** Approved

## Problem

`ghr-pypi` writes a directory of documents and stops. Every deployment context
then needs hand-written glue: cache headers on Cloudflare, a server block on
nginx, and — for private repositories — a token-holding redirector that no
static host can provide at all. None of that is expressible today, and the
`mirror: bool` key cannot grow a third asset strategy.

## Programme

This is four sub-projects. **This spec covers sub-project 1 only.**

| # | Sub-project | Delivers on its own |
|---|---|---|
| 1 | **Target interface + asset modes** | `Target` protocol, registry, `assets` and `target` config keys, `static`/`cloudflare`/`nginx` targets emitting their artifacts |
| 2 | **Redirector asset mode** | `assets: redirect`; generated Worker / nginx redirector with Basic auth; private repositories served without mirroring |
| 3 | **Webhook rebuilds** | Generated receiver Worker validating the HMAC and firing `repository_dispatch` |
| 4 | **Tutorial rewrite and slimming** | Cloudflare tutorial reoriented around the secured Worker setup; all tutorials cut to the ghr-pypi-specific parts |

Webhooks matter most when aggregating *other* repositories — a release in your
own repository already fires locally — so (3) pairs with the pattern-expansion
work. (4) is last because it documents 1-3.

## Two findings that shape the programme

**pip cannot authenticate to Cloudflare Access.** Access wants SSO cookies or
`CF-Access-Client-Id`/`CF-Access-Client-Secret` headers, and pip and uv cannot
send arbitrary per-index headers — the same wall that stops them fetching
private assets directly. They *can* do HTTP Basic (URL credentials, netrc,
keyring) and client certificates. So in sub-project 2 the Worker implements
Basic auth itself; Access is documented as the complement for humans browsing
the landing page, explicitly not as the gate for installers.

**The redirector is mostly already built.** `FileEntry.api_url` already carries
each asset's API endpoint, added for mirror mode's authenticated downloads.
`GET /repos/.../releases/assets/<id>` with a bearer token and
`Accept: application/octet-stream` answers 302 to a signed URL needing no
headers. So sub-project 2 is a URL-rewrite plus a generated script, not new
data collection.

## The orthogonality

`assets` and `target` are independent axes, and keeping them independent is
what makes the interface extensible:

- **`assets`** decides what URLs the index contains.
- **`target`** decides what deployment artifacts are written alongside.

`assets: redirect` on nginx and on Cloudflare produce the *same* index; only
the artifact implementing the redirect differs. The redirect URL shape is
therefore target-independent, and **`Target` never rewrites URLs** — it only
emits files. Without this rule, sub-project 2 turns the protocol into a grab
bag.

## `assets` replaces `mirror`

```yaml
assets: link      # default — link to GitHub's download URLs
assets: mirror    # download into <out>/files/ and link relatively
```

`redirect` is added in sub-project 2. `Config.mirror: bool` becomes
`Config.assets: AssetMode`, where `AssetMode = Literal["link", "mirror"]` lives
in `config.py` beside the existing `MissingDigest` and `Formats` aliases.
`targets` may import it from there: `config` imports neither `index` nor
`targets`, so `targets → index → config` and `targets → config` are both
acyclic.

Migration, since `mirror` shipped in 2026.8.6:

- `mirror: true` still loads, emits a deprecation warning on stderr, and maps
  to `assets: mirror`. `mirror: false` maps to `assets: link`.
- Both keys present is a `ConfigError` — silently preferring one would hide a
  contradiction.
- `--mirror` on the command line keeps working, maps to `assets: mirror`, and
  is still rejected alongside `--config`.
- The existing rule that `missing_digest` is rejected under mirroring now keys
  on `assets == "mirror"`; its message is unchanged.

## The interface

New package `src/ghr_pypi/targets/`: `__init__.py` (protocol, `SiteContext`,
registry), `static.py`, `cloudflare.py`, `nginx.py`. `index.py` is already
~600 lines and deployment knowledge does not belong in it.

```python
@dataclass(frozen=True)
class SiteContext:
    projects: Projects
    out_dir: Path       # published
    target_dir: Path    # operator artifacts — never published
    title: str
    index_url: str | None
    assets: AssetMode
    formats: tuple[Formats, ...]


class Target(Protocol):
    name: str

    def emit(self, site: SiteContext) -> None:
        """Write this target's deployment artifacts."""
```

One method, one context object. The context is passed rather than N arguments
so that adding a field later does not break a third-party target.

### `out_dir` versus `target_dir`

This split is load-bearing, not tidiness. An nginx snippet written into
`out_dir` would be **published** — you would serve your own server
configuration — and in sub-projects 2-3 the same mistake would publish auth
logic. Operator artifacts therefore go to `target_dir`, a new `--target-out`
option defaulting to the working directory:

```sh
ghr-pypi index --target nginx        # ./ghr-pypi.conf, site in ./_site/
```

Cloudflare's `_headers` is a genuine site file and stays in `out_dir`. Each
target decides which directory an artifact belongs in; the protocol supplies
both and documents the distinction.

### Registry

```python
def get_target(name: str) -> Target
def available_targets() -> dict[str, Target]
```

Built-ins live in a dict, merged with entry points in group
`ghr_pypi.targets`. **Built-ins win on a name collision, with a warning** — a
transitive dependency must never silently change what a build emits.

### Where validation lives

`config.load` checks only that `target` is a string. Resolving the name against
the registry happens in the CLI, raising `ConfigError` that lists the available
names.

This is forced as well as consistent: `targets` needs `Projects` from `index`,
and `index` imports `config`, so validating in `config` would close a cycle. It
also matches the rule already established for repository patterns — `load`
parses and validates shape, the CLI resolves anything needing the outside
world.

## The three built-in targets

**`static`** (default) emits nothing. It names the current behavior so that
"no target" is a choice rather than an absence.

**`cloudflare`** writes `out_dir/_headers`:

- `/files/*` — `Cache-Control: public, max-age=31536000, immutable`. Mirrored
  files are content-addressed by filename and never change.
- `/simple/*` — `Cache-Control: public, max-age=300, must-revalidate`. The
  index changes on every release.
- `/files/*.metadata` — explicit `Content-Type: application/octet-stream`,
  which Pages would otherwise guess wrong for an unknown extension.

The `/files/*` rules are emitted only under `assets: mirror`; there are no
local files otherwise.

**`nginx`** writes `target_dir/ghr-pypi.conf`: a `server`-body snippet with
`root`, `autoindex off`, a `types` block mapping `.metadata` to
`application/octet-stream`, and `try_files $uri $uri/index.html =404` for the
PEP 503 trailing-slash convention. It is a snippet for inclusion, not a
complete `nginx.conf`, and says so in a leading comment.

## Config surface

```yaml
target: cloudflare     # static (default) | cloudflare | nginx | a plugin name
assets: mirror         # link (default) | mirror
```

Both are optional. `target` accepts any registered name, so the error message
must enumerate what is registered rather than hard-code three names.

On the command line, `--target` follows the `--mirror` rule and is **rejected
alongside `--config`** — with a config file, the file is the whole description
of the build. `--target-out` is a path like `--out`, not a behavioral switch, so
it is allowed in both forms and has no config-file equivalent.

## Tests

- Registry: built-ins present; unknown name raises listing available names;
  entry-point discovery finds a stub target; a plugin colliding with a built-in
  loses and warns.
- `SiteContext` is frozen and its fields are what the protocol documents.
- Each target's `emit` is a pure function of context to files: build a context,
  emit into `tmp_path`, assert exact file contents.
- `cloudflare` omits the `/files/*` rules under `assets: link` and includes
  them under `assets: mirror`.
- **`nginx` writes nothing into `out_dir`** — asserted explicitly, because
  publishing the server config is the failure this split exists to prevent.
- `static` writes nothing anywhere.
- Migration: `mirror: true` loads with a warning and yields `assets == "mirror"`;
  `mirror: false` yields `"link"`; both keys present raises; `--mirror` maps to
  `assets: mirror`; `missing_digest` is still rejected under mirroring.
- CLI end-to-end: `--target cloudflare` writes `_headers`; `--target nginx`
  writes `ghr-pypi.conf` into `--target-out`; the default writes neither.
- **The CLI wiring is mutation-proven** — deleting the `target.emit(...)` call
  must fail a test.

## Docs

- `reference/configuration.rst`: `target` and `assets` keys, the `mirror`
  deprecation, every new error message verbatim.
- `reference/cli.rst`: `--target`, `--target-out`, and that `--mirror` is now
  an alias.
- A new reference page for the target interface: the protocol, `SiteContext`,
  the `out_dir`/`target_dir` rule, and how to ship a third-party target via the
  entry point.
- A how-to for writing a custom target.
- Changelog, including the deprecation.

## Out of scope

- `assets: redirect`, Workers, Basic auth, webhooks — sub-projects 2 and 3.
- Rewriting the tutorials — sub-project 4.
- Deploying anything. The tool writes artifacts; `wrangler` and `nginx -s
  reload` remain the operator's.
- `_redirects` for Cloudflare: Pages already resolves directory URLs to
  `index.html`, so an empty file would be noise.
