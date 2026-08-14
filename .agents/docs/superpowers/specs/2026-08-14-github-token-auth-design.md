# GitHub-token authentication for the Cloudflare redirector — design

**Date:** 2026-08-14
**Status:** approved in conversation (gate = index repo, sidecars behind the same gate,
signed-URL cache removed, cloudflare-only opt-in mode); this document is the written form
for review.

## Goal

An opt-in authentication mode for the `cloudflare` target under `assets: redirect` in which
clients present **their own fine-grained GitHub PAT** as the Basic-auth password. The Worker
authorizes index pages by asking GitHub whether that token can read a **gate repository**
(the index repository itself), and authorizes downloads by forwarding the client's token to
GitHub's asset API — so GitHub, which already owns the per-repository ACL, decides who can
download what. Per-user identity, per-user revocation, per-repository granularity, and a
Worker that holds **no token at all**.

The existing shared-credential mode remains the default and is untouched.

## Config surface

One new top-level config key:

```yaml
auth: basic | github        # default: basic
```

- `auth: basic` (or the key absent): exactly today's behavior.
- `auth: github` requires `assets: redirect` and a target that declares
  `supports_github_auth = True` (only `cloudflare`; mirrors the existing
  `supports_redirect` refusal pattern — `nginx` cannot validate a PAT and is refused with a
  clear error).
- New optional key `gate_repository: OWNER/NAME` names the gate. When absent under
  `auth: github`, it defaults to `$GITHUB_REPOSITORY` (set in any Actions run — for the
  template, that *is* the index repository); if neither is available the build exits 1:
  `error: auth: github needs gate_repository (or $GITHUB_REPOSITORY)`.
- The gate value is validated with the same `OWNER/NAME` shape rule the webhook command
  applies to `--index-repo` (no dot-segments, no separators beyond one `/`), because it is
  interpolated into generated JavaScript and into a GitHub API path.

## How the Worker learns the mode and the gate

**Baked into the generated `site/_worker.js` at build time**, not bound as Cloudflare vars.
The source `_worker.js` gains a sentinel block:

```js
// Replaced by the build. An unreplaced sentinel means shared-credential mode.
const AUTH_MODE = "%%GHR_PYPI_AUTH%%";
const GATE_REPO = "%%GHR_PYPI_GATE%%";
```

with resolution logic: `AUTH_MODE === "github"` selects token auth; anything else selects
basic. The cloudflare target's `emit()` performs the substitution when writing
`site/_worker.js` — in **both** modes, so no `%%` sentinel ever ships.

**Test-only env fallback**: when a sentinel is *unreplaced* (only true for the pristine
source under test — emit always substitutes), the value falls back to
`env.GHR_PYPI_AUTH_MODE` / `env.GHR_PYPI_GATE_REPO`. This lets the node suite drive both
modes through the original file (the coverage gate requires exercising these branches in
the real `_worker.js`, not a rewritten temp copy), while a deployed Worker's mode can never
be flipped by a binding: its sentinels are already substituted.

Why baked rather than `[vars]` in `wrangler.toml`: the values are not secrets, they are
per-build facts; baking makes the deployed site self-contained, keeps the deploy a plain
`wrangler pages deploy` with no dependency on wrangler applying Pages `[vars]` from a
regenerated toml, and leaves nothing to configure in a dashboard. Neither value is
sensitive.

## Request handling in `auth: github` mode

Every request must carry `Authorization: Basic <base64>`; the login half is ignored
(document "your GitHub username, or anything"), the password half is the PAT.

1. **Gate check** (index pages, landing page, manifest, and `.metadata` sidecars — i.e.
   everything served rather than redirected): the Worker calls
   `GET https://api.github.com/repos/<GATE_REPO>` with `Bearer <client PAT>`.
   - `200` → serve the request.
   - `401`/`403`/`404` → `401` with the existing `WWW-Authenticate: Basic` challenge
     (from GitHub, "cannot see the gate repo" and "bad token" are both `404`/`401`; to the
     client they are the same fact: not authorized for this index).
   - Anything else (GitHub 5xx, network failure) → `502 Upstream error` — fail closed, and
     do not miscue the user into rotating a working token.
   - **No caching of the verdict**: one extra subrequest per served page, paid from the
     client's own 5,000 req/hr rate limit. pip touches a handful of pages per install;
     acceptable by design, and removable later if it ever isn't.
2. **Downloads** (`/_assets/<id>/<file>`): unchanged manifest allow-list check first, then
   the asset API request is made with `Bearer <client PAT>` instead of a bound secret.
   - `302` → relay the signed URL, as today.
   - `401`/`403`/`404` from GitHub → `403 Forbidden` with a one-line body ("your token
     cannot read this repository") — *after* the gate check has passed, this is a
     per-repository authorization verdict, not an outage, and must not read as a `502`.
     The catalog is already visible to anyone past the gate, so this hides nothing.
   - Anything else non-302 → `502`, as today.
3. **Fail closed**: if `AUTH_MODE` is `github` but `GATE_REPO` is missing or fails the
   shape rule at runtime, every request gets `500 Worker is not configured` (same posture
   as the webhook receiver).
4. `GHR_PYPI_USER` / `GHR_PYPI_PASSWORD` / `GHR_PYPI_TOKEN` bindings are ignored in this
   mode and need not exist.

## The signed-URL cache is removed — from both modes

Today the Worker caches GitHub's signed URL per asset for 60s. Under per-user tokens that
cache is a privilege-escalation bug (user B would receive a URL minted with user A's
authorization, bypassing B's per-repo check). Rather than key it per token, it is deleted
outright: one code path, a simpler worker, and the cost — one extra GitHub subrequest per
download — is negligible at package-index traffic. The `CACHE_SECONDS`/`openCache`/
`cachedLocation`/`cacheLocation` machinery goes away; response `Cache-Control` headers are
unaffected.

## What does not change

- `assets: redirect` link shape, manifest generation and its allow-list role, PEP 658
  sidecar placement, response headers, the nginx target, the webhook receiver, and
  `auth: basic` end-to-end behavior (minus the removed URL cache).
- `GHR_PYPI_TOKEN` is still required **at build time** — the Actions workflow reads
  releases with it. It simply stops being bound to the Worker in github mode.

## Template and docs

- **Template** (`cloudflare-pypi-template`): `ghr-pypi.yml` documents `auth: github` as a
  commented-out option. `deploy.yml`'s config-check step greps the effective auth mode from
  `ghr-pypi.yml`; in github mode it stops requiring `GHR_PYPI_USER`/`GHR_PYPI_PASSWORD` and
  the secret-binding step binds nothing. README gains a mode comparison (shared password:
  simplest, one credential; github: per-user, per-repo, revoke in GitHub) and per-mode
  netrc instructions.
- **SECURITY.md** (template): credentials table gains the github-mode row — the Worker
  holds no token; each client's PAT is their own blast radius; catalog visibility = read
  access to the index repository.
- **Docs**: `configuration` reference (the two new keys), `private-packages-without-
  mirroring` how-to (a "Per-user tokens" section: gate semantics, error mapping, the
  disclosure caveat that anyone past the gate sees the whole catalog), tutorial gets a
  short pointer note, changelog entry.

## Testing

- **Node worker tests**: drive github mode through the env fallback
  (`GHR_PYPI_AUTH_MODE`/`GHR_PYPI_GATE_REPO` in the test env object), importing the
  original module so coverage counts. Cases: gate 200 serves pages/sidecars; gate 404/401 → 401 challenge; gate 5xx → 502;
  download forwards the client's Bearer token verbatim; download 404-from-GitHub → 403;
  manifest miss still 404 before any GitHub call; sentinel unreplaced → exact current
  basic behavior; github mode with malformed gate → 500; no signed-URL cache: two identical
  downloads hit the asset API twice (this replaces the current cache tests).
- **Python tests**: config accepts/defaults/rejects the new keys (`auth: github` + nginx →
  error; bad `gate_repository` shape → error; default from `$GITHUB_REPOSITORY`); emitted
  `_worker.js` has the sentinels substituted (anchored, per the vacuous-config-test
  lesson: assert the exact `const AUTH_MODE = "github";` line, and that no `%%` sentinel
  survives in the emitted file); basic-mode emission leaves behavior unchanged.

## Out of scope

- Per-user filtering of index *pages* (the catalog is all-or-nothing behind the gate).
- Token-validation caching.
- nginx support for this mode.
- Any webhook receiver changes.
