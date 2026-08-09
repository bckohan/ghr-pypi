# Webhook-Driven Rebuilds — Design

**Date:** 2026-08-09
**Status:** Approved

## Problem

A release in the index's own repository fires a `release` event and `pages.yml`
rebuilds. A release in any *other* repository does not. Since pattern expansion
made aggregating whole owners easy, an index can now go stale for days with
nothing to notice it.

## Programme position

Sub-project **3 of 4**. Sub-projects 1 (target interface) and 2 (redirect
assets) are complete. Remaining: (4) the tutorial rewrite and Cloudflare
reorientation.

## The finding that shapes it

**You cannot put a webhook on a repository you do not administer.** Creating a
repository webhook requires admin on that repository; an org webhook requires
org admin. So the case where staleness hurts most — aggregating repositories
you do not control — cannot be solved by webhooks at all.

That splits the problem into three, with genuinely different answers:

| Situation | Answer | Kind |
|---|---|---|
| Repos you control | Their release workflow calls the dispatch API | configuration |
| A whole org you admin | One org webhook → a receiver → the dispatch API | **software** |
| Repos you do not control | A scheduled rebuild | configuration |

The difference between rows 1 and 2 is **who initiates the API call**. In row 1
a runner is already executing in the source repo, so it can make the call
itself. In row 2 nothing is running: a webhook is GitHub POSTing to a URL, and
a URL needs a listener. No GitHub feature is that listener.

Row 2 cannot be collapsed into row 1 — pasting the row-1 snippet into every
repo is row 1 done N times, and it misses repos created later. One setup
covering every current *and future* repo is the org webhook's whole value, and
that is exactly what forces a listener to exist.

## The trigger

`pages.yml` gains:

```yaml
on:
  repository_dispatch:
    types: [ghr-pypi-rebuild]
```

Chosen over calling `workflow_dispatch` through the API: it is purpose-built
for external triggers, needs only `contents: write` on the index repo rather
than `actions: write`, and keeps "a human clicked Run" distinguishable from "a
release happened elsewhere" in the run history.

`pages.yml`'s existing `concurrency: {group: pages, cancel-in-progress: true}`
already collapses a burst of dispatches into a single rebuild, which matters
when an org publishes several packages at once.

## Row 1 — documented snippet

The source repo's release workflow gains a step calling
`gh api repos/OWNER/INDEX/dispatches -f event_type=ghr-pypi-rebuild` with a
token carrying `contents: write` on the index repo. `GITHUB_TOKEN` cannot do
this — it is scoped to the repository it runs in — so a fine-grained PAT or App
installation token is required, and the docs must say so.

## Row 3 — documented recipe

A `schedule:` block in `pages.yml`. The only option for repos you do not
control. Documented with its two honest costs: staleness is bounded by the
interval rather than eliminated, and **GitHub disables scheduled workflows in
repositories with no activity for 60 days**, which silently stops the rebuilds.

## Row 2 — the receiver

### Shape: a standalone subcommand

`ghr-pypi webhook --index-repo OWNER/NAME [--out DIR]` writes `worker.js`,
`wrangler.toml` and `SETUP.md` into `DIR` (default `webhook`). It parallels
`extract-meta` — a producer-side utility, not part of a site build. The command
itself lives in `cli.py` beside `extract-meta`.

**Names, because two Workers now exist and one collision is easy to make:**
the packaged source is `src/ghr_pypi/webhook_worker.js` — *not* another
`_worker.js`, which would collide with the redirector's package data. It is
emitted as `worker.js` and named by `main = "worker.js"` in `wrangler.toml`.
The `_worker.js` filename is a Cloudflare *Pages* convention for advanced mode;
this is a standalone Worker, where the entrypoint is whatever `main` says, so
the leading underscore would be cargo-culted.

`--index-repo` is required and validated with the existing `check_repository`,
and is baked into `wrangler.toml` as a `[vars]` entry. Taking it as an argument
rather than leaving a placeholder means the regenerated file stays correct;
`wrangler.toml` is rewritten on every run, and the redirector already
demonstrated that inviting hand-edits into a regenerated file loses them.

It is **not** a `cloudflare` target artifact. Someone on plain GitHub Pages with
`target: static` needs the receiver too, and requiring `target: cloudflare` to
obtain it would be wrong.

### A separate Worker from the redirector

The redirector requires Basic auth on **every** request. GitHub's webhook POST
cannot send Basic auth, so hosting the receiver there would mean carving an
exemption out of the auth boundary that protects a private index — where a bug
exposes the index. Two Workers, two auth models, no interaction.

### What it does, in order

1. Non-`POST` → 405.
2. Read the **raw body text first**. The HMAC is computed over raw bytes, so
   parsing before verifying would verify something other than what was signed.
3. `X-Hub-Signature-256` → HMAC-SHA256 via `crypto.subtle` keyed with
   `WEBHOOK_SECRET`, compared in constant time. Missing, malformed or wrong
   → 401.
4. `X-GitHub-Event: ping` → 200. GitHub pings on hook creation, and a receiver
   that rejects the ping looks broken in the UI long before a release happens.
5. `X-GitHub-Event: release` → dispatch. Any other event → 204, ignored.
6. `POST /repos/{INDEX_REPO}/dispatches` with `event_type: ghr-pypi-rebuild`
   and a `client_payload` naming the source repository, so the run log records
   why it ran. Success → 202. Failure → 502 with GitHub's body never forwarded.

The token must appear in no response on any path.

### Deliberately absent

**No replay protection and no repository allow-list.** A replayed or irrelevant
dispatch causes a rebuild, which is idempotent and re-reads the config — so the
cost is a wasted build, not a wrong index. Both would need state or
configuration for no correctness gain. Documented rather than built.

### Secrets and vars

`WEBHOOK_SECRET` and `GITHUB_TOKEN` as secrets; `INDEX_REPO` as a plain var in
`wrangler.toml`. `SETUP.md` must use `wrangler pages secret put` only if the
receiver is deployed as a Pages project — it is a standalone Worker, so the
correct commands are `wrangler secret put` and `wrangler deploy`. **This is the
inverse of the redirector's, and the implementer must verify both against
Cloudflare's current command reference rather than copying either.**

## Tests

**Node**, mirroring the redirector's suite: valid signature accepted; tampered
body rejected; missing and malformed signature headers rejected; non-`POST`
→ 405; ping → 200; a non-release event → 204 with no dispatch; a release event
→ dispatch called once with the right URL, event type and payload; dispatch
failure → 502; and the token absent from every response on every path.

The constant-time comparison needs a **same-length** wrong signature — the
redirector's suite initially tested only different-length credentials, so the
comparison loop was never exercised and `return true` passed everything.

**Python**: the subcommand writes all three files into `--out`, creates the
directory, bakes `--index-repo` into `wrangler.toml`, rejects a malformed
`--index-repo`, and emits `worker.js` byte-identical to the packaged
`webhook_worker.js`. A test must also assert the redirector's `_worker.js` is
**not** what gets written — the two are one careless `resources.files(...)`
argument apart.

## Docs

A how-to, "How do I rebuild when another repository releases?", covering all
three rows and stating plainly which applies when — including that row 2 is
unavailable without org admin and row 3 is the only answer for third-party
repositories. Plus `reference/cli.rst` for the new subcommand, the changelog,
and a `direction.md` update.

## Out of scope

- An nginx or self-hosted receiver.
- Rebuild coalescing beyond what the existing `concurrency` block provides.
- Any change to what a rebuild does; this only decides *when* one starts.
- The tutorial rewrite — sub-project 4.
