# GitHub-token authentication — implementation plan

> Spec: `.agents/docs/superpowers/specs/2026-08-14-github-token-auth-design.md`
> Executed in-session, task by task, verify after each. Nothing is committed (driver rule).

**Goal:** `auth: github` mode for the cloudflare redirector: clients present their own
fine-grained PAT as the Basic password; the Worker gates served pages on the token's read
access to a gate repository and forwards the token to GitHub for downloads. Signed-URL
cache removed from both modes.

## Task 1 — Worker (`src/ghr_pypi/targets/_worker.js`)

- Sentinel block + `buildConfig(env)` resolution (baked value wins; unreplaced sentinel
  falls back to `env.GHR_PYPI_AUTH_MODE`/`env.GHR_PYPI_GATE_REPO` for tests only).
- `clientToken(request)`: Basic header → password half, null on absent/malformed/empty.
- `gateRefusal(token)`: `GET api.github.com/repos/<gate>` with Bearer; 200 → null,
  401/403/404 → `unauthorized()`, else/throw → `badGateway()`.
- `forbidden()` 403 and `misconfigured()` 500 helpers.
- fetch(): github mode → validate gate shape (REPO regex + `..`) else 500; token else 401;
  gate check before every served response (pages, manifest, static sidecars); downloads
  skip the gate, use `Bearer <client token>`, map GitHub 401/403/404 → 403, other → 502.
- Delete `CACHE_SECONDS`/`openCache`/`cachedLocation`/`cacheLocation`; `redirectTo` keeps
  `private, max-age=60` literal. Update the header comment (secrets per mode).

**Verify:** task 2's suite; `just test-worker` green with coverage ≥95/90.

## Task 2 — Node tests (`tests/worker/worker.test.mjs`)

Replace cache tests with no-cache assertions (two identical downloads → two upstream
calls, both modes). Add github-mode block driven via env fallback: gate 200 serves; gate
401/404 → 401 challenge; gate 5xx/throw → 502; no/bad/empty Authorization → 401 with no
GitHub call; download forwards client Bearer verbatim; download 404 → 403 body; manifest
miss → 404 before any GitHub call; static sidecar behind gate; malformed gate env → 500;
basic-mode secrets ignored in github mode.

**Verify:** `just test-worker` — all pass, coverage thresholds hold.

## Task 3 — Config (`src/ghr_pypi/config.py`)

`AuthMode` literal; keys `auth`, `gate_repository` in `_KNOWN_KEYS`; `Config.auth`
(default "basic"), `Config.gate_repository` (default None). Validation: auth ∈
{basic, github}; `auth: github` requires `assets: redirect`; `gate_repository` requires
`auth: github`, slug shape + dot-segment refusal.

## Task 4 — CLI (`src/ghr_pypi/cli.py`)

After target resolution: `supports_github_auth` refusal mirroring the redirect one. Gate
default: `auth: github` with no `gate_repository` → `$GITHUB_REPOSITORY` (validated) or
exit 1 `error: auth: github needs gate_repository (or $GITHUB_REPOSITORY)`. Pass
`auth`/`gate_repository` through `SiteContext`.

## Task 5 — Targets (`targets/__init__.py`, `targets/cloudflare.py`)

`SiteContext.auth`/`.gate_repository` fields (defaults keep plugins working); protocol
docstring notes `supports_github_auth`; cloudflare declares it and `emit()` substitutes
both sentinels via `json.dumps` (both modes — no `%%` survives). SETUP.md gains
`{secrets}`/`{symptoms}` sections varying by mode; update the "copied verbatim" comment.

**Verify (3–5):** new config/cli/target tests; `just test tests/test_cli.py
tests/test_targets.py`.

## Task 6 — Python tests (`tests/test_cli.py`, `tests/test_targets.py`)

Config: default, valid, auth-without-redirect, bad value, gate-without-github, bad-gate
shapes. CLI: nginx+github refusal names cloudflare; missing gate exits 1; env fallback
bakes gate. Targets: emitted worker has exact `const AUTH_MODE = "github";` /
`const GATE_REPO = "owner/repo";` lines and no `%%` in either mode (anchored, per the
vacuous-test lesson); SETUP has no `secret put` in github mode.

## Task 7 — Docs

`reference/configuration.rst` (two keys), `how-to/private-packages-without-mirroring.rst`
("Per-user GitHub tokens" section: gate semantics, 401 vs 403 vs 502 map, catalog
disclosure caveat), tutorial pointer note, `reference/targets` contract mention,
changelog entry (includes cache removal).

**Verify:** `just check-docs && just build-docs-html`.

## Task 8 — Template (`../cloudflare-pypi-template`)

`ghr-pypi.yml`: commented `auth: github` option. `deploy.yml`: config-check greps auth
mode; github mode drops the USER/PASSWORD requirement and skips the bind step.
`README.md`: mode comparison + per-mode netrc. `SECURITY.md`: github-mode row.

**Verify:** yaml parse, `zizmor --persona auditor` clean.

## Task 9 — Full verification

`just check` (lint, types, docs, readme), `just test` (450+), `just test-worker` (81+ →
more), template zizmor. Report.
