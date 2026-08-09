# Webhook-Driven Rebuilds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **PROJECT RULE (AGENTS.md):** agents never commit or push — the human driver
> does. Implementers stop at "verified in working tree".

**Goal:** A release in another repository rebuilds the index — by workflow snippet where you control the repo, by org webhook and a generated receiver where you admin the org, and by schedule where you control neither.

**Architecture:** `pages.yml` accepts `repository_dispatch`. A new `ghr-pypi webhook` subcommand emits a standalone Cloudflare Worker that validates GitHub's HMAC and calls the dispatch API. The other two routes are configuration and are documented, not shipped.

**Spec:** `.agents/docs/superpowers/specs/2026-08-09-webhook-rebuilds-design.md` (sub-project 3 of 4)

**Context for the implementer:**
- Current: 391 Python tests, 51 worker tests, `just check-all` exit 0.
- **Do NOT run `just test-all <path>`** — it destroys the project venv.
- **Leave nothing untracked in the repo root.**
- Read `AGENTS.md` at the repo root before starting.
- The redirector Worker lives at `src/ghr_pypi/targets/_worker.js` with tests in
  `tests/worker/worker.test.mjs`. **This is a different Worker.** Read the
  existing one for house style, then keep them separate.
- `just test-worker` already globs `tests/worker/**/*.test.mjs`, so a new test
  file there is picked up automatically and counted in coverage.

---

### Task 1: The receiver Worker and its node tests

**Goal:** A reviewable, tested webhook receiver.

**Files:**
- Create: `src/ghr_pypi/webhook_worker.js`, `tests/worker/webhook.test.mjs`

**Acceptance Criteria:**
- [ ] Non-`POST` → 405
- [ ] Any unbound secret or var → 500, never "accept everything"
- [ ] The raw body is read **before** parsing; the HMAC covers those bytes
- [ ] Missing, malformed or wrong `X-Hub-Signature-256` → 401
- [ ] `ping` → 200; a non-`release` event → 204 with no dispatch
- [ ] `release` → one dispatch to `/repos/{INDEX_REPO}/dispatches` with `event_type: ghr-pypi-rebuild` and a `client_payload` naming the source repo
- [ ] Dispatch failure or network throw → 502, GitHub's body never forwarded
- [ ] The token appears in no response on any path
- [ ] A **same-length** wrong signature is rejected — the comparison loop must be exercised

**Verify:** `just test-worker` → all pass

**Steps:**

- [ ] **Step 1: Write `src/ghr_pypi/webhook_worker.js`.**

```js
/**
 * ghr-pypi webhook receiver: turns a GitHub release event into a
 * repository_dispatch that rebuilds the index.
 *
 * A standalone Worker, deliberately separate from the redirector in
 * targets/_worker.js: that one demands Basic auth on every request and
 * GitHub's webhook POST cannot send it, so hosting this there would mean
 * punching a hole in the auth boundary protecting a private index.
 *
 * Secrets: WEBHOOK_SECRET, GITHUB_TOKEN.  Var: INDEX_REPO.
 */

const EVENT_TYPE = "ghr-pypi-rebuild";

function text(status, body) {
  return new Response(`${body}\n`, {
    status,
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
}

function constantTimeEqual(a, b) {
  // Length is not secret; the loop below is what must not short-circuit.
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function signatureFor(secret, body) {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, encoder.encode(body));
  const hex = [...new Uint8Array(mac)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
  return `sha256=${hex}`;
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return text(405, "Method not allowed");
    if (!env.WEBHOOK_SECRET || !env.GITHUB_TOKEN || !env.INDEX_REPO) {
      // Fail closed: an unbound secret must never mean "accept everything".
      return text(500, "Receiver is not configured");
    }

    // The signature covers the raw bytes, so read the body before parsing it.
    const body = await request.text();
    const provided = request.headers.get("X-Hub-Signature-256") || "";
    const expected = await signatureFor(env.WEBHOOK_SECRET, body);
    if (!constantTimeEqual(provided, expected)) return text(401, "Bad signature");

    const event = request.headers.get("X-GitHub-Event") || "";
    // GitHub pings on hook creation; rejecting it makes the hook look broken
    // in the UI long before anyone publishes a release.
    if (event === "ping") return text(200, "pong");
    if (event !== "release") return new Response(null, { status: 204 });

    let payload;
    try {
      payload = JSON.parse(body);
    } catch {
      return text(400, "Malformed payload");
    }
    const source =
      payload && payload.repository && payload.repository.full_name
        ? payload.repository.full_name
        : "unknown";

    let response;
    try {
      response = await fetch(
        `https://api.github.com/repos/${env.INDEX_REPO}/dispatches`,
        {
          method: "POST",
          headers: {
            Accept: "application/vnd.github+json",
            Authorization: `Bearer ${env.GITHUB_TOKEN}`,
            "Content-Type": "application/json",
            "User-Agent": "ghr-pypi-webhook",
          },
          body: JSON.stringify({
            event_type: EVENT_TYPE,
            client_payload: { repository: source },
          }),
        },
      );
    } catch {
      return text(502, "Dispatch failed");
    }
    if (!response.ok) {
      // Never forward GitHub's body or headers: they can echo request detail.
      return text(502, "Dispatch failed");
    }
    return text(202, "Rebuild requested");
  },
};
```

**Review this as you type it.** If you find a flaw, fix it and say so in your report rather than transcribing faithfully — the redirector's draft had six.

- [ ] **Step 2: Write `tests/worker/webhook.test.mjs`.** Follow `tests/worker/worker.test.mjs` for harness style. A helper must compute a *real* signature, or every test would pass against a broken verifier:

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import worker from "../../src/ghr_pypi/webhook_worker.js";

const SECRET = "s3cret";
const ENV = { WEBHOOK_SECRET: SECRET, GITHUB_TOKEN: "tok-abc", INDEX_REPO: "o/idx" };

function sign(body) {
  return "sha256=" + createHmac("sha256", SECRET).update(body).digest("hex");
}

function post(body, { event = "release", signature, method = "POST" } = {}) {
  const headers = { "X-GitHub-Event": event };
  if (signature !== null) headers["X-Hub-Signature-256"] = signature ?? sign(body);
  return new Request("https://hook.example.com/", { method, headers, body });
}

function stubDispatch({ ok = true, throws = false } = {}) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    if (throws) throw new Error("network down");
    return new Response(ok ? "{}" : "nope", { status: ok ? 204 : 403 });
  };
  return calls;
}

const RELEASE = JSON.stringify({ action: "published", repository: { full_name: "o/lib" } });
```

Cover every acceptance criterion, plus:
- a **same-length** wrong signature (mutate one hex character) — a different-length one never reaches the comparison loop
- the dispatch body actually contains `ghr-pypi-rebuild` and `o/lib`
- the token appears in no response header or body, on **every** path including 401, 405, 500, 502

- [ ] **Step 3: Verify.** `just test-worker` → all pass (report the count); `just check-all > /tmp/gate.log 2>&1; echo EXIT=$?` → `EXIT=0`.

- [ ] **Step 4: Mutations (do not skip).** Reverting each with `Edit`:
  1. `constantTimeEqual` → `return true` → confirm a test fails, and confirm it is the **same-length** test doing it.
  2. Delete the unbound-secret guard → confirm a test fails.
  3. Parse the body before verifying the signature → confirm a test fails (tamper a body whose signature was computed over the original).
  4. Return `response` directly instead of `text(502, …)` → confirm the token-leak test fails.

  Report all four.

*(Driver checkpoint: commit as "Add the webhook receiver Worker")*

---

### Task 2: The `ghr-pypi webhook` subcommand

**Goal:** `ghr-pypi webhook --index-repo o/idx` writes a deployable receiver.

**Files:**
- Modify: `src/ghr_pypi/cli.py`, `tests/test_cli.py`

**Acceptance Criteria:**
- [ ] `ghr-pypi webhook --index-repo OWNER/NAME` writes `worker.js`, `wrangler.toml`, `SETUP.md` into `--out` (default `webhook`), creating it
- [ ] `worker.js` is byte-identical to the packaged `webhook_worker.js`
- [ ] **It is not the redirector's `_worker.js`** — asserted explicitly
- [ ] `--index-repo` is required, must be `OWNER/NAME`, and **must not be a pattern**
- [ ] `wrangler.toml` carries `main = "worker.js"` and `INDEX_REPO` set to the given repo
- [ ] Every written path is echoed
- [ ] `webhook_worker.js` ships in the wheel

**Verify:** `just test` → all pass (report the count)

**Steps:**

- [ ] **Step 1: Failing tests** in `tests/test_cli.py`, reusing `runner`, `app`, `all_output`:

```python
def test_webhook_writes_a_deployable_receiver(tmp_path):
    out = tmp_path / "hook"
    result = runner.invoke(app, ["webhook", "--index-repo", "o/idx", "--out", str(out)])
    assert result.exit_code == 0, all_output(result)
    packaged = (
        resources.files("ghr_pypi").joinpath("webhook_worker.js").read_text(encoding="utf-8")
    )
    assert (out / "worker.js").read_text(encoding="utf-8") == packaged
    wrangler = (out / "wrangler.toml").read_text(encoding="utf-8")
    assert 'main = "worker.js"' in wrangler
    assert 'INDEX_REPO = "o/idx"' in wrangler
    assert (out / "SETUP.md").exists()


def test_webhook_does_not_write_the_redirector(tmp_path):
    # The two Workers are one careless resources.files(...) argument apart.
    out = tmp_path / "hook"
    runner.invoke(app, ["webhook", "--index-repo", "o/idx", "--out", str(out)])
    redirector = (
        resources.files("ghr_pypi.targets").joinpath("_worker.js").read_text(encoding="utf-8")
    )
    assert (out / "worker.js").read_text(encoding="utf-8") != redirector


@pytest.mark.parametrize("value", ["nope", "*/idx", "o/id*", ""])
def test_webhook_rejects_a_bad_index_repo(tmp_path, value):
    result = runner.invoke(
        app, ["webhook", "--index-repo", value, "--out", str(tmp_path / "hook")]
    )
    assert result.exit_code == 1
    assert not (tmp_path / "hook").exists()


def test_webhook_requires_an_index_repo(tmp_path):
    result = runner.invoke(app, ["webhook", "--out", str(tmp_path / "hook")])
    assert result.exit_code == 2
```

Add `from importlib import resources` if absent.

- [ ] **Step 2: Implement** in `src/ghr_pypi/cli.py`, beside `extract-meta`. Import `check_slug` and `is_pattern` from `ghr_pypi.config` if not already imported, and `from importlib import resources`.

```python
_WEBHOOK_WRANGLER = """\
# Generated by ghr-pypi. REGENERATED ON EVERY RUN — re-apply any edit.
name = "ghr-pypi-webhook"
main = "worker.js"
compatibility_date = "2026-08-09"

[vars]
INDEX_REPO = "{index_repo}"
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

    Point an organization webhook at the deployed Worker. Use this only for
    repositories you administer: creating a webhook needs admin on the
    repository or the organization.
    """
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
    out.mkdir(parents=True, exist_ok=True)
    worker = (
        resources.files("ghr_pypi").joinpath("webhook_worker.js").read_text(encoding="utf-8")
    )
    for name, content in (
        ("worker.js", worker),
        ("wrangler.toml", _WEBHOOK_WRANGLER.format(index_repo=index_repo)),
        ("SETUP.md", _WEBHOOK_SETUP.format(index_repo=index_repo)),
    ):
        target = out / name
        target.write_text(content, encoding="utf-8")
        typer.echo(f"wrote {target}")
```

**Validate before creating the directory** — the tests assert a rejected `--index-repo` leaves no directory behind.

`_WEBHOOK_SETUP` must give, in order: `wrangler secret put WEBHOOK_SECRET`, `wrangler secret put GITHUB_TOKEN`, `wrangler deploy`; then creating the org webhook (payload URL, content type `application/json`, the same secret, **Releases events only**); and that the token needs `contents: write` on `{index_repo}`.

**Verify those command spellings against Cloudflare's current reference.** This is a standalone **Worker**, so `wrangler secret put` and `wrangler deploy` are expected to be right — which is the *inverse* of the redirector's Pages project, where `wrangler pages secret put` is required. Getting that wrong last time produced a deployment that 401'd everything. Report what you checked.

- [ ] **Step 3: Confirm the wheel ships it.** `src/ghr_pypi/webhook_worker.js` sits directly under the package, which hatchling's `packages = ["src/ghr_pypi"]` already includes — verify empirically by building into the scratchpad and listing the wheel, and add nothing to `pyproject.toml` unless it is genuinely missing.

- [ ] **Step 4: Verify.** `just test` (report the count); `just fix`; `just check-types`.

- [ ] **Step 5: Mutations.** Reverting each with `Edit`:
  1. Point `resources.files(...)` at `ghr_pypi.targets` / `_worker.js` → confirm `test_webhook_does_not_write_the_redirector` fails.
  2. Move `out.mkdir(...)` above the validation → confirm a rejection test fails.

  Report both.

*(Driver checkpoint: commit as "Add the ghr-pypi webhook subcommand")*

---

### Task 3: The trigger, the docs, and the gate

**Goal:** `pages.yml` accepts the dispatch, and all three routes are documented.

**Files:**
- Modify: `.github/workflows/pages.yml`, `doc/source/reference/cli.rst`, `doc/source/how-to/index.rst`, `doc/source/changelog.rst`, `README.md`, `direction.md`
- Create: `doc/source/how-to/rebuild-on-release.rst`

**Acceptance Criteria:**
- [ ] `pages.yml` has `repository_dispatch: types: [ghr-pypi-rebuild]` and its header comment lists it
- [ ] `cli.rst` documents `webhook` with its options and exit-1 conditions verbatim
- [ ] The new how-to covers all three routes and says which applies when
- [ ] It states that **webhooks need admin** on the repo or org, so row 2 is unavailable otherwise and row 3 is the only answer for third-party repos
- [ ] It states that `GITHUB_TOKEN` cannot dispatch cross-repo — a PAT or App token is required
- [ ] It states that **GitHub disables scheduled workflows after 60 days of repository inactivity**
- [ ] `just check-all` → exit 0 and a warning-free strict HTML build

**Verify:** `just check-all > /tmp/gate.log 2>&1; echo EXIT=$?` → `EXIT=0`

**Steps:**

- [ ] **Step 1: `pages.yml`.** Add to `on:`:

```yaml
  repository_dispatch:
    types: [ghr-pypi-rebuild]
```

and extend the file's header comment, which currently enumerates the triggers, to mention that another repository's release can request a rebuild this way. Change nothing else.

- [ ] **Step 2: `doc/source/reference/cli.rst`.** Document `webhook` in the same shape as `extract-meta`: synopsis, what it writes where, `--index-repo` and `--out`, and the exit-1 conditions verbatim from `src/ghr_pypi/cli.py`. Update the page's opening, which says how many commands there are.

- [ ] **Step 3: Create `doc/source/how-to/rebuild-on-release.rst`,** titled as a question like its siblings. Lead with the three-row table so a reader self-selects, then a section each:
  - **Repos you control** — the workflow snippet, and that `GITHUB_TOKEN` cannot do it.
  - **An org you admin** — `ghr-pypi webhook`, deploy, the two secrets, creating the org webhook with Releases events only.
  - **Repos you control neither** — the `schedule:` block, with staleness bounded by the interval and the 60-day inactivity disable.

  Say plainly that a spurious rebuild is harmless because it is idempotent and re-reads the config, which is why there is no replay protection or repository allow-list. Add it to the how-to toctree.

- [ ] **Step 4:** `README.md` — one line in the feature list, keeping lines short (that region is MyST-included). **Step 5:** `changelog.rst` — bullets for the subcommand and the trigger. **Step 6:** `direction.md` — the webhook item is done; narrow the remaining entry to the nginx redirector.

- [ ] **Step 7: Gate.**

```
just fix
just test
just test-worker
just check-all > /tmp/gate.log 2>&1; echo EXIT=$?
uv run --no-default-groups --group docs sphinx-build -b html -a -E -n ./doc/source /tmp/wh-docs
```

`EXIT=0` and a warning-free strict build. Then:

```bash
grep -rn "repository_dispatch\|ghr-pypi webhook" --include='*.rst' --include='*.md' --include='*.yml' doc/source README.md .github
git status --short
```

Judge every hit and report both.

*(Driver checkpoint: commit as "Document webhook-driven rebuilds")*

---

## After the plan

Driver: commit the checkpoints. Sub-project 4 is the tutorial rewrite and the
Cloudflare reorientation, which now has both the secured setup and the rebuild
trigger to build on.
