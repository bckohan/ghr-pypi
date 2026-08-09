# The Tutorial Set — Design

**Date:** 2026-08-09
**Status:** Approved

## Problem

Three tutorials exist and each opens with roughly 120 lines that have nothing
to do with this library: create a Python package, write a release workflow,
push, publish a release. The same material three times, drifting
independently. `nginx.rst` is 532 lines and spends a step on certbot.

Meanwhile the set has gaps and staleness. Nothing teaches indexing
*other* repositories, which is the case pattern expansion was built for.
`cloudflare.rst` teaches mirror mode with cache-header tuning and closes with
a section describing edge access control as hypothetical — "two things become
possible with a few lines of code" — when the Worker redirector has shipped
since sub-project 2 and the webhook receiver since sub-project 3. `nginx.rst`
teaches a static site with an optional password, written before the nginx
redirector existed.

## Programme position

Sub-project **2 of 2** in the tutorial decomposition, and the last of the
four-part deployment programme. Sub-project 1 (the nginx redirector) is
complete and is what tutorial 4 is written against.

## The constraint that shapes it

**Tutorials assume the reader already has a GitHub repository whose release
process posts wheels to Releases.** Nothing is built for them. Every tutorial
starts where `ghr-pypi` starts.

This is not a shared prerequisites page. The prerequisite is *external* — it
is the reader's existing repository, not something the documentation
constructs — so there is nothing to factor out and each tutorial remains
genuinely self-contained. The index's existing promise ("follow any single
tutorial start to finish, without reading the others") survives unchanged, and
the "What you will need" block each tutorial already has is where the
assumption is stated.

## The four

| File | Teaches | Target |
|---|---|---|
| `github-pages.rst` | one workflow, zero config | ~10 min |
| `other-repositories.rst` *(new)* | a config file; the index stops describing its own repo | ~15 min |
| `cloudflare.rst` *(rewritten)* | `assets: redirect` + Worker + webhook rebuilds | ~30 min |
| `nginx.rst` *(rewritten)* | `assets: redirect` on stock nginx; mirror as the alternative | ~25 min |

### 1. Publish on GitHub Pages

The zero-configuration case, and the shortest possible path to a working
index. `ghr-pypi index` defaults its repository to `$GITHUB_REPOSITORY` and its
output to `_site`, so the tutorial is one `pages.yml`, enabling Pages, and
installing from the result.

Ends with the reader running `pip install --index-url` against their own index.

### 2. Index other repositories

The mental shift this teaches: the index no longer describes the repository it
lives in. That is a different model from tutorial 1, not a variation on it,
which is why it is a tutorial rather than only a how-to.

Progression: a `repositories:` list of two repos → an `owner/*` pattern →
`exclude_repositories`. Must state that with `--config` the repositories live
in the config file and passing them as arguments as well is an error.

Must also state that the workflow's built-in `GITHUB_TOKEN` is scoped to the
repository it runs in, so indexing *private* repositories elsewhere needs a
fine-grained PAT or App token. A reader who skips this gets an index that
silently omits everything it could not read.

Closes by pointing at `rebuild-on-release` for the staleness problem it has
just created, since an index of other people's repositories does not rebuild
when they release.

### 3. A private index on Cloudflare

The reorientation. `assets: redirect` with `target: cloudflare`, which emits
`_worker.js` into the site and `wrangler.toml` plus `SETUP.md` beside it.

Order matters and is the inverse of the receiver's: `wrangler pages project
create` → `wrangler pages secret put` ×3 (`GHR_PYPI_USER`,
`GHR_PYPI_PASSWORD`, `GITHUB_TOKEN`) → `wrangler pages deploy`. Secrets bind
to a project, so it must exist first, and a secret added after a deployment
needs a redeploy to take effect.

Then installing with credentials in `~/.netrc`, and why Cloudflare Access is
not an alternative: pip cannot send the `CF-Access-Client-Id` and
`CF-Access-Client-Secret` headers it requires, which is why the Worker does
Basic auth itself.

Then the second half: `ghr-pypi webhook --index-repo`, deploying the receiver
as a standalone Worker (`wrangler deploy` → `wrangler secret put` ×2 — deploy
first, the inverse of the Pages order, because `wrangler secret put` against a
Worker that does not exist silently publishes a placeholder), creating the
organization webhook with Releases events only, and the `repository_dispatch`
trigger in `pages.yml`.

The two halves are one story: a private aggregating index that goes stale is
the exact failure the webhook exists to fix. **If the tutorial runs long, cut
the webhook half to a pointer at `rebuild-on-release` rather than thinning the
security setup.**

Must state that creating an organization webhook requires being an
organization *owner*, not merely an admin.

### 4. A private index on nginx

`assets: redirect` with `target: nginx`, against the redirector from
sub-project 1.

The shape: `ghr-pypi-assets.conf` included at `http` level and `ghr-pypi.conf`
inside `server` — two files because `map` is only valid in `http` context. The
two credential files the build deliberately refuses to write
(`ghr-pypi-token.conf` and the htpasswd), and why: a build tool that put a
token in its own output would put it wherever that output goes.

**`nginx -s reload` after every build.** The map is compiled at configuration
load, so a running server keeps serving the previous one. This is the single
most important operational fact in the tutorial and the one place the two
redirectors differ: Cloudflare picks up a release on redeploy. The failure is
silent — the index page lists a package whose downloads 404 while every older
package works.

Closes with **mirror as the alternative**, given a section rather than a
parenthetical because it is the main decision an nginx operator makes:

| | Redirect | Mirror |
|---|---|---|
| Where assets live | GitHub | the server |
| Token at request time | required | none |
| Storage | none | every wheel, every version |
| After a release | reload | resync |

## What is dropped, and where it goes

- **Package creation, push, first release** — assumed, per the constraint above.
- **`cloudflare.rst`'s mirror-mode cache-header tuning** — `customize-pages`
  and the reference cover it, and mirror mode is no longer what that tutorial
  is about.
- **`cloudflare.rst`'s "What Workers would add"** — deleted outright. It
  describes as hypothetical the thing the tutorial now does.
- **`nginx.rst`'s content negotiation** (one URL serving HTML and :pep:`691`
  JSON) — good material, but `json-api` is its home. Link, do not teach.
- **`nginx.rst`'s certbot step** — dropped. Obtaining a TLS certificate is not
  a `ghr-pypi` task and is the largest non-library chunk in the set.
- **`nginx.rst`'s optional password step** — absorbed. In redirect mode
  authentication is load-bearing from the first request, not a final flourish.

Nothing dropped may simply vanish: each item above either has a how-to home
that must be linked, or is deliberately out of scope and must not be replaced
by a vague gesture at it.

## `tutorials/index.rst`

The "Which one should I do?" table grows to four rows. The self-containment
paragraph gains the shared prerequisite. The recommendation for a reader with
no preference stays `github-pages`, since it is still the shortest route to a
working index and the other three vary it.

## Tests

Documentation, so the gate is `just check-all` at exit 0 and a warning-free
strict HTML build (`sphinx-build -b html -a -E -n`), plus `doc8`.

Beyond the build: **every command, filename, path, secret name and config key
in the four tutorials must be checked against what the code actually emits.**
Generate a real bundle for tutorials 3 and 4 and read the emitted `SETUP.md`
and `ghr-pypi.conf` rather than trusting the prose. This project has shipped a
README config example that exits 1, a secret-ordering procedure that was wrong
in both directions, and a `_headers` claim contradicted by Cloudflare's own
documentation.

## Out of scope

- Any code change. If a tutorial cannot be written without one, that is a
  finding to report, not a licence to make it.
- The how-to guides, except for links into them.
- `_redirects` alongside `_headers` — the remaining `direction.md` item.
