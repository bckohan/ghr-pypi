# Redirect Asset Mode — Design

**Date:** 2026-08-08
**Status:** Approved

## Problem

Private repositories cannot be indexed usefully today. pip and uv cannot send
the `Authorization` and `Accept` headers GitHub's asset API requires, and
`browser_download_url` needs a browser session. The only working answer is
`assets: mirror`, which copies every wheel into the site — expensive, and it
duplicates bytes GitHub is already storing.

A thin token-holding redirector solves it: the client makes a plain GET, a
server-side component calls the asset API with a stored token, and GitHub
answers **302 to a short-lived signed URL that needs no headers**. pip follows
that happily.

## Programme position

Sub-project **2 of 4**. Sub-project 1 (the `Target` interface, `assets`
key, and the `cloudflare`/`nginx` targets) is complete. Remaining: (3) webhook
rebuilds, (4) the tutorial rewrite and slimming.

## The finding that shapes it

The obvious redirector takes an asset ID from the URL and calls
`GET /repos/{owner}/{repo}/releases/assets/{id}`. That endpoint needs owner and
repo, so the path must carry them — and then **anyone who can reach the index
can ask the redirector to fetch any asset the token can read**, including from
repositories that were never indexed. The redirector becomes a general-purpose
exfiltration proxy for the token's entire read scope.

So the build emits a manifest and the redirector serves only what it lists.
This also removes owner and repo from the URL, and — because the redirector
then holds no build data — lets it be deployed **once**, with every subsequent
release being an ordinary Pages deploy.

## `assets: redirect`

`AssetMode` becomes `Literal["link", "mirror", "redirect"]`. Each file's URL
becomes site-relative, matching mirror mode so the site stays movable:

```
../../_assets/<asset-id>/<filename>
```

Rewriting happens in a dedicated `index.redirect_urls(projects)`, called by the
CLI — the same shape as `mirror_files` rewriting entries for mirror mode.

### The one place `assets` and `target` are not orthogonal

`redirect` needs a cooperating artifact, so `assets: redirect` with a target
that provides no redirector is a build that emits paths nothing serves.

The CLI checks an **optional** `supports_redirect` attribute on the resolved
target — `getattr(selected, "supports_redirect", False)` — and errors naming
the targets that do provide one. Optional with a `getattr` default so every
third-party target written against sub-project 1 keeps working, and a
third-party redirector can declare itself. `CloudflareTarget` sets it `True`;
`static` and `nginx` do not.

## The manifest

`<out>/_assets/manifest.json`, written by the **build** whenever
`assets: redirect` — not by the Cloudflare target, because an nginx redirector
later needs exactly the same data. Keeping it at the index layer is what makes
the second implementation cheap.

```json
{
  "version": 1,
  "assets": {
    "123456": {
      "repo": "yourorg/lib",
      "filename": "lib-1.0-py3-none-any.whl",
      "metadata_id": 123457
    }
  }
}
```

`metadata_id` is present only when the release carries a `.metadata` sidecar.

The redirector refuses any ID not listed, **and** checks the filename in the
path against the entry — a listed ID must not be fetchable under an arbitrary
name. The manifest sits behind the same Basic auth as everything else, but it
does list repository names; that is documented, not hidden.

### Data needed on `FileEntry`

`api_url` and `source_repo` already exist. `collect_projects` currently records
a paired sidecar's *digest* but not its identity, so it gains
`metadata_api_url: str` (empty when there is no sidecar), symmetric with
`api_url`. Both IDs are the trailing path segment of their api_url.

## The Worker is a shipped file

`src/ghr_pypi/targets/_worker.js` is a real file in the package, copied
verbatim into `out_dir`. Generating JavaScript from Python string templates
would make the most security-sensitive code in the project unreviewable and
untestable.

It does three things:

1. **Basic auth** on every request — index and assets alike. An unauthenticated
   index leaks the package inventory even when the files are gated. Credentials
   come from the `GHR_PYPI_USER` / `GHR_PYPI_PASSWORD` secrets, compared in
   constant time. A missing or wrong credential returns 401 **with
   `WWW-Authenticate: Basic`** — pip needs that header to consult netrc or
   keyring.
2. **Manifest lookup** through the assets binding, rejecting unknown IDs and
   filename mismatches with 404.
3. **The token call** — `GET /repos/.../releases/assets/<id>` with
   `Authorization: Bearer` from the `GITHUB_TOKEN` secret and
   `Accept: application/octet-stream` — returning GitHub's 302 unchanged.

**Everything else is delegated to `env.ASSETS.fetch(request)`.** An advanced-mode
`_worker.js` intercepts *every* request, so static content is served only if the
Worker passes it through. That is how the index pages, the manifest and the
extracted `.metadata` files reach the client at all, and it is why authentication
in the Worker genuinely gates the whole site rather than just `/_assets/`.

The Location is cached for 60 seconds. Signed URLs live around five minutes,
and a CI fleet installing the same wheel would otherwise spend GitHub's
5,000/hour limit on identical lookups. Caching a redirect is safe here: it is
the same file for every authorised client, and authorisation happens first.

The response must never carry the token or the raw GitHub error body.

## PEP 658 metadata

Redirect mode is the only mode where PEP 658 works for private repositories
without mirroring the wheels.

- **Sidecar present** → `metadata_id` is in the manifest and the redirector
  serves `<file-url>.metadata` by redirecting to it. No download.
- **Sidecar absent** → the build downloads that wheel once, extracts
  `*.dist-info/METADATA`, and writes a real static file at
  `_assets/<id>/<filename>.metadata`. The path is ours, so nothing else is
  needed. New `index.extract_missing_metadata(projects, out_dir, token)`,
  reusing `read_wheel_metadata`.

### `missing_metadata`

```yaml
missing_metadata: extract    # default | warn
```

`warn` suppresses the download and emits the same per-repository warning link
mode already produces.

Following the `missing_digest` precedent exactly, **setting it outside redirect
mode is an error, not a silent no-op**. It cannot take effect elsewhere: mirror
mode already extracts from a wheel it downloaded anyway, and link mode *cannot*
extract, because the metadata would have to live at GitHub's `<url>.metadata`,
which we cannot write.

## Artifacts

| File | Directory | Why |
|---|---|---|
| `_assets/manifest.json` | `out_dir` | read by the redirector through the assets binding |
| `_assets/<id>/<file>.metadata` | `out_dir` | served statically; only for wheels with no sidecar |
| `_worker.js` | `out_dir` | Pages consumes it at deploy and does not serve it |
| `wrangler.toml` | `target_dir` | operator artifact; must not be published |
| `SETUP.md` | `target_dir` | the three `wrangler secret put` commands and the deploy line |

## Tests

**Python.** URL rewriting for every entry; manifest contents including
`metadata_id` present and absent; `supports_redirect` gating, with the error
naming the targets that qualify; `missing_metadata` validation including the
outside-redirect-mode error; extraction firing only for sidecar-less wheels and
not at all under `warn`; the Cloudflare target emitting `_worker.js` verbatim
(byte-compare against the packaged file) and `wrangler.toml`/`SETUP.md` into
`target_dir` and nothing into `out_dir` beyond `_worker.js` and `_headers`.

**The Worker**, with `node --test` driving its exported `fetch(request, env)`
against a stubbed manifest and a stubbed GitHub, run from a `just` recipe that
**skips cleanly when node is absent** rather than failing:

- no credentials → 401 carrying `WWW-Authenticate: Basic`
- wrong credentials → 401
- correct credentials, unknown ID → 404
- correct credentials, ID present but filename mismatched → 404
- happy path → 302 with the signed Location
- `.metadata` path with `metadata_id` → 302 to the sidecar
- `.metadata` path without `metadata_id` → delegated to `env.ASSETS`, which is
  what serves the extracted static file (**not** a 404 — the Worker sees this
  request first, so it must pass it through rather than reject it)
- an index page path (`/simple/foo/`) → delegated to `env.ASSETS`, proving the
  site is served at all rather than swallowed by the redirector
- the token appears in no response header or body, on any path
- a second request inside the cache window makes no second GitHub call

## Docs

- `reference/configuration.rst`: `assets: redirect`, `missing_metadata`, every
  new error verbatim.
- `reference/cli.rst`: the new exit-1 conditions.
- `reference/targets.rst`: `supports_redirect` as an optional protocol
  attribute.
- A how-to: "How do I serve private packages without mirroring?" — the config,
  `wrangler deploy`, the three secrets, and the pip side (`netrc`, or
  credentials in the index URL).
- The security notes: what the manifest reveals, why the index is gated too,
  and that Cloudflare Access is **not** a substitute because pip cannot send
  its headers.
- Changelog.

## Out of scope

- The nginx redirector. It can consume the same manifest via a generated `map`
  file and `proxy_pass`, but it is a second auth model and a second set of live
  tests; it gets its own pass once the Worker settles the contract.
- Multiple credentials or per-consumer revocation.
- Deploying anything. `wrangler` remains the operator's.
- Webhook rebuilds and the tutorial rewrite — sub-projects 3 and 4.
