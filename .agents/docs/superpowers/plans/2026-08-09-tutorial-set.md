# The Tutorial Set Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **PROJECT RULE (AGENTS.md):** agents never commit or push — the human driver
> does. Implementers stop at "verified in working tree".

**Goal:** Four tutorials that teach `ghr-pypi` and nothing else, against a reader who already has a repository publishing wheels to Releases.

**Architecture:** `github-pages.rst` and a new `other-repositories.rst` cover public indexes; `cloudflare.rst` and `nginx.rst` are rewritten around `assets: redirect`, which did not exist when they were written. Every tutorial loses its package-creation, push and first-release steps.

**Spec:** `.agents/docs/superpowers/specs/2026-08-09-tutorial-set-design.md`

**Context for the implementer:**
- **NEVER run `git checkout`** — much of the tree is uncommitted. Revert with `Edit`.
- **Do NOT run `just test-all <path>`** — it forwards arguments to `uv run` as *flags*, not to pytest as paths, and with `--isolated --exact` it destroys the project venv. Use `just test <path>`.
- **No code changes.** If a tutorial cannot be written without one, report it as a finding. Do not make it.
- Read `AGENTS.md` at the repo root before starting.
- Leave nothing untracked in the repo root (`direction.md` is gitignored — expected).

**The governing constraint, which applies to all four tasks:**

> Tutorials assume the reader already has a GitHub repository whose release process posts wheels to Releases.

Nothing is built for them. Every tutorial begins where `ghr-pypi` begins. Each keeps its existing `What you will need` block, restated around that assumption. The prerequisite is *external* — the reader's own repository — so nothing is factored into a shared page and each tutorial stays self-contained.

**Verify every fact against the code, not the prose.** Every command, filename, path, secret name and config key must match what the code actually emits. Generate real output for Tasks 3 and 4 and read it. This project has shipped a README config example that exits 1, a secret-ordering procedure wrong in both directions, and a `_headers` claim contradicted by Cloudflare's own docs.

**Generating that output needs a token.** `ghr-pypi index` always requires one, even for a public repository. `bckohan/ghr-pypi` is a public repo with real releases and works as the subject. If `GITHUB_TOKEN` is not set in your environment and `gh auth token` yields nothing, **say so and stop rather than writing the tutorial from this plan** — the emitted `SETUP.md` and `ghr-pypi.conf` are the source of truth for Tasks 3 and 4, and prose invented in their absence is exactly the failure mode this project keeps hitting. The target artifacts alone (`wrangler.toml`, `SETUP.md`, the two nginx files) can also be inspected by reading `src/ghr_pypi/targets/*.py` and `src/ghr_pypi/cli.py`, which is a valid fallback — but read them, do not guess.

**House style:** read two existing how-tos before writing. Tutorials are titled as statements ("Publish on GitHub Pages"); how-tos as questions. Steps are `Step N — Title`. Lines wrap under 100 characters (`doc8` enforces it). Each tutorial ends with `What you built` and `Where to go next`.

---

### Task 1: Trim `github-pages.rst`

**Goal:** The shortest path to a working index, starting at the workflow.

**Files:**
- Modify: `doc/source/tutorials/github-pages.rst`, `doc/source/tutorials/index.rst`

**Acceptance Criteria:**
- [ ] Steps 1 (create the package), 2 (release workflow) and 4 (push) are gone
- [ ] `What you will need` states the external prerequisite: a GitHub repository whose release process already publishes wheels to Releases
- [ ] The `pages.yml` from the current Step 3 survives **verbatim**, as do its four explanatory paragraphs
- [ ] Remaining steps renumber contiguously from 1
- [ ] `tutorials/index.rst`'s self-containment paragraph names the shared prerequisite
- [ ] The file drops below 200 lines (from 379)

**Verify:** `uv run --no-default-groups --group docs sphinx-build -b html -a -E -n ./doc/source /tmp/t1` → 0 warnings

**Steps:**

- [ ] **Step 1: Read the whole file first.** `doc/source/tutorials/github-pages.rst`, all 379 lines. You are deleting three steps and renumbering; everything else is being kept, and the explanatory prose after the `pages.yml` block is the most valuable content on the page.

- [ ] **Step 2: Rewrite `What you will need`** to state the prerequisite. It must say the reader needs a GitHub repository whose release process already attaches wheels to its Releases, and that this tutorial does not set that up. Keep whatever tool prerequisites the existing block lists that still apply.

- [ ] **Step 3: Delete Steps 1, 2 and 4** — "Create the package", "Add the release workflow", "Push the repository to GitHub". Renumber the survivors from 1: the Pages workflow, turning on Pages, publishing a release, watching the workflows, looking at what you built, installing.

  **Keep the `pages.yml` code block byte-for-byte** and keep all four paragraphs after it ("Four things about this file are worth noticing…"). They explain the zero-argument form, that the build never checks out, why a token is always required, and why it does not run on push. That is the actual teaching.

- [ ] **Step 4: `tutorials/index.rst`.** The paragraph currently promises "each is self-contained: follow any single tutorial start to finish… Everything you need to type is on the page". Amend it to state that all four assume a repository already publishing wheels to Releases, and that beyond that each is self-contained. Do not weaken the self-containment claim further — it is still true.

- [ ] **Step 5: Verify.**

```
uv run --no-default-groups --group docs sphinx-build -b html -a -E -n ./doc/source /tmp/t1 2>&1 | tail -5
wc -l doc/source/tutorials/github-pages.rst
```

Expect `build succeeded`, zero warnings, and under 200 lines. Then `just check-all > /tmp/gate.log 2>&1; echo EXIT=$?` → `EXIT=0`.

*(Driver checkpoint: commit as "Trim the GitHub Pages tutorial to the ghr-pypi steps")*

---

### Task 2: `other-repositories.rst`

**Goal:** A tutorial teaching that the index need not describe the repository it lives in.

**Files:**
- Create: `doc/source/tutorials/other-repositories.rst`
- Modify: `doc/source/tutorials/index.rst`, `doc/source/how-to/rebuild-on-release.rst`

**Acceptance Criteria:**
- [ ] Progression: a `repositories:` list of two → an `owner/*` pattern → `exclude_repositories`
- [ ] States that with `--config`, repositories live in the config file and passing them as arguments **as well** is an error — quote the real message
- [ ] States that the workflow's built-in `GITHUB_TOKEN` is scoped to the repository it runs in, so **private** repositories elsewhere need a fine-grained PAT or App token
- [ ] Closes by pointing at `rebuild-on-release` for the staleness it has just created
- [ ] Added to the toctree **and** the "Which one should I do?" table
- [ ] Every config key used appears in `doc/source/reference/configuration.rst`

**Verify:** strict sphinx build → 0 warnings; every YAML block parses

**Steps:**

- [ ] **Step 1: Establish the facts before writing.** Read `src/ghr_pypi/config.py` for the real key names and `src/ghr_pypi/cli.py:_resolve_config` / `_expand_patterns` for the real behaviour and error messages. Confirm by running:

```
uv run ghr-pypi index owner/repo --config some.yml 2>&1 | head -2
```

Quote the error verbatim rather than paraphrasing it.

- [ ] **Step 2: Write the tutorial.** Title it as a statement, in the tutorials' voice. Structure:

  - `What you will need` — the external prerequisite, plus: for private repositories, a token that can read them.
  - `Step 1 — Write a configuration file` — `repositories:` with two entries. Show the full YAML.
  - `Step 2 — Point the workflow at it` — the `pages.yml` delta: `uvx ghr-pypi index --config ghr-pypi.yml`. Show only what changes from tutorial 1's workflow, and say that is the only change.
  - `Step 3 — Match a whole owner with a pattern` — replace the list with `owner/*`. Explain that expansion lists what the token can see, so the same pattern gives a different index to a token with wider access.
  - `Step 4 — Exclude what you do not want` — `exclude_repositories`. State that exclusions apply to pattern expansions only: a repository named explicitly is always indexed.
  - `Step 5 — Install from it`
  - `What you built`
  - `Where to go next` — `rebuild-on-release` first, because an index of other people's repositories does not rebuild when they release. Then `aggregate-repositories` and `index-an-organization`.

- [ ] **Step 3: The token warning is not optional.** Somewhere before Step 3 the tutorial must state plainly that `GITHUB_TOKEN` is scoped to the repository the workflow runs in. A reader who misses this gets an index that silently omits every private repository it could not read — no error, just a shorter index. Say that consequence, not only the rule.

- [ ] **Step 4: `tutorials/index.rst`.** Add `other-repositories` to the toctree after `github-pages`, and a row to the "Which one should I do?" table with a time and a one-line "what it teaches". Keep the existing recommendation that a reader with no preference does `github-pages` first.

- [ ] **Step 4a: One caveat in `doc/source/how-to/rebuild-on-release.rst:9-11`.** It asserts:

  > Publishing a release in the repository that *hosts* the index fires a ``release`` event there, and its Pages workflow runs.

  That is false when the release is created by a workflow using the built-in `GITHUB_TOKEN` — GitHub does not raise `release` for token-created releases, and `workflow_dispatch` / `repository_dispatch` are the only unconditional exceptions. Confirmed against [triggering a workflow](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow), and `doc/source/tutorials/github-pages.rst` now carries a note about it.

  **This is a one-clause caveat, not a rewrite.** The page's load-bearing claim is the *contrast* — that a release in another repository fires nothing in the index repo at all — which is unconditionally true, and row one already mandates a PAT or App token, which sidesteps the suppression anyway. Add the qualification and change nothing else on that page.

- [ ] **Step 5: Verify.** Strict sphinx build → 0 warnings. Then check every YAML block actually parses:

```
uv run --no-project --with pyyaml python -c "
import re, sys, yaml
text = open('doc/source/tutorials/other-repositories.rst').read()
for i, block in enumerate(re.findall(r'\.\. code-block:: yaml\n\n((?:   .*\n|\n)+)', text)):
    yaml.safe_load(block)
    print(f'block {i} ok')
"
```

Report the count. A tutorial whose config examples do not parse is worse than none.

*(Driver checkpoint: commit as "Add the other-repositories tutorial")*

---

### Task 3: Rewrite `cloudflare.rst`

**Goal:** A private index behind the Worker, rebuilt by an organization webhook.

**Files:**
- Modify: `doc/source/tutorials/cloudflare.rst`

**Acceptance Criteria:**
- [ ] Teaches `assets: redirect` with `target: cloudflare`, not mirror mode
- [ ] Deploy order is `wrangler pages project create` → `wrangler pages secret put` ×3 → `wrangler pages deploy`, with the reason
- [ ] Names the three secrets exactly as the Worker reads them
- [ ] States that Cloudflare Access is not an alternative, and why
- [ ] Second half: `ghr-pypi webhook`, deployed with `wrangler deploy` **then** `wrangler secret put` ×2 — the inverse order, with the reason
- [ ] States that an organization webhook requires an organization **owner**
- [ ] "What Workers would add" is **deleted**, not revised
- [ ] Mirror-mode cache-header tuning is gone, with a link to `customize-pages`

**Verify:** strict sphinx build → 0 warnings; every command matches generated output

**Steps:**

- [ ] **Step 1: Generate the real artifacts and read them.** Do not write from the plan.

```
mkdir -p /tmp/cf && cd /tmp/cf
```

Build an index with `assets: redirect` and `target: cloudflare` against any public repo with wheels, then read the emitted `wrangler.toml` and `SETUP.md`. Also run `ghr-pypi webhook --index-repo yourorg/pypi --out /tmp/cf/hook` and read its `SETUP.md`. **The tutorial must agree with these files.** They are generated, tested, and regenerated per index; the tutorial is not. Where they overlap, prefer pointing at them over restating them.

- [ ] **Step 2: Read `src/ghr_pypi/targets/_worker.js`** for the exact secret names and failure behaviour, and `doc/source/how-to/private-packages-without-mirroring.rst`, which is the how-to this tutorial is the guided version of.

- [ ] **Step 3: Rewrite.** Keep the `What you will need`, `What you built` and `Where to go next` skeleton. Steps:

  - `Step 1 — Turn on redirect mode` — the config: `assets: redirect`, `target: cloudflare`. Say what changes: links point at the site's own `_assets/` paths, and the site now needs something that can serve them.
  - `Step 2 — Build the site` — and look at what appeared: `_worker.js` in the site, `wrangler.toml` and `SETUP.md` beside it. Explain the split: the Worker is consumed by Pages at deploy time; the other two must never be published.
  - `Step 3 — Create the Pages project`
  - `Step 4 — Bind the three secrets` — before the first deploy. Secrets attach to a project, so it must exist first, and one added after a deployment needs a redeploy.
  - `Step 5 — Deploy`
  - `Step 6 — Install from it` — `~/.netrc`, and why not Cloudflare Access.
  - `Step 7 — Rebuild when another repository releases` — the receiver.
  - `What you built`, `Where to go next`.

- [ ] **Step 4: The two orders are inverse and that is the trap.** The Pages project needs its secrets *before* the first deploy. The standalone receiver Worker must be *deployed first*, because `wrangler secret put` against a Worker that does not exist prompts to create one and defaults to yes non-interactively, silently publishing a placeholder. State both, and state that they differ.

- [ ] **Step 5: Delete "What Workers would add"** (currently around line 403). It presents edge access control as hypothetical — "two things become possible with a few lines of code" — which is exactly what this tutorial now does. Deleting it is the point of the rewrite; do not reword it.

- [ ] **Step 6: Verify.** Strict sphinx build → 0 warnings. Then diff your commands against the generated files:

```
grep -n "wrangler" doc/source/tutorials/cloudflare.rst
grep -n "wrangler" /tmp/cf/SETUP.md /tmp/cf/hook/SETUP.md
```

Every `wrangler` invocation in the tutorial must appear in, or be consistent with, the generated checklists. Report both lists and judge each difference.

*(Driver checkpoint: commit as "Reorient the Cloudflare tutorial around the redirector")*

---

### Task 4: Rewrite `nginx.rst`

**Goal:** A private index on stock nginx, with mirror presented as the alternative.

**Files:**
- Modify: `doc/source/tutorials/nginx.rst`
- Modify: `doc/source/changelog.rst`

**Acceptance Criteria:**
- [ ] Teaches `assets: redirect` with `target: nginx`
- [ ] Both files at their contexts — `ghr-pypi-assets.conf` in `http`, `ghr-pypi.conf` in `server` — and that `map` being http-only is why there are two
- [ ] The two credential files the build refuses to write, and why
- [ ] **`nginx -s reload` after every build**, with the silent-failure consequence
- [ ] The three fail-closed behaviours stated accurately and distinctly
- [ ] Closes with a mirror-vs-redirect section including the trade table
- [ ] certbot step, content-negotiation step and optional-password step are gone; content negotiation links to `json-api`
- [ ] Changelog bullet for the tutorial set
- [ ] File drops well below 532 lines

**Verify:** `just check-all` → `EXIT=0`; strict sphinx build → 0 warnings

**Steps:**

- [ ] **Step 1: Generate and read the real config.** Build an index with `assets: redirect` and `target: nginx`, then read both emitted files in full. Their header comments are the operator instructions — the tutorial's job is to walk a reader through them, not to restate them. Report anything the comments say that the tutorial then contradicts.

- [ ] **Step 2: Rewrite.** Steps:

  - `Step 1 — Turn on redirect mode` — `assets: redirect`, `target: nginx`.
  - `Step 2 — Build the index` — and look at the two emitted files.
  - `Step 3 — Install the configuration` — `ghr-pypi-assets.conf` inside `http { }`, `ghr-pypi.conf` inside `server { }`. Say why: `map` is only valid in http context, so one file cannot be included at both.
  - `Step 4 — Create the two files the build will not write` — `ghr-pypi-token.conf` and the htpasswd. State the reason: a build tool that put a token in its own output would put it wherever that output goes.
  - `Step 5 — Ship the site and reload`
  - `Step 6 — Install from it`
  - `After every release` — a section of its own, not a footnote. See Step 3 below.
  - `Mirror instead?` — the closing fork.
  - `What you built`, `Where to go next`.

- [ ] **Step 3: `nginx -s reload` gets its own section.** The map is compiled at configuration load, so a running server keeps serving the previous one until reloaded. State the failure concretely: the index page lists a newly published package, downloads of it 404, and every older package keeps working — so nothing looks broken until someone tries to install the new one. Say that this is the one place the two redirectors differ, because Cloudflare picks a release up on redeploy.

- [ ] **Step 4: The three failures are different and the difference matters.** From the generated comments, verified during the redirector work:
  - missing `ghr-pypi-token.conf` → `nginx -t` fails, nginx will not start
  - wrong `proxy_ssl_trusted_certificate` path → also fails at configuration load
  - missing htpasswd → `nginx -t` **passes**, nginx starts, requests get 403

  Do not flatten these into "nginx refuses to start". Two of the three do; the one that does not is the one an operator will actually hit.

- [ ] **Step 5: The closing fork.** A short section, with this table:

```rst
.. list-table::
   :header-rows: 1

   * -
     - Redirect
     - Mirror
   * - Where assets live
     - GitHub
     - the server
   * - Token at request time
     - required
     - none
   * - Storage
     - none
     - every wheel, every version
   * - After a release
     - reload
     - resync
```

Say which to pick and when, rather than listing the options neutrally.

- [ ] **Step 6: Removals.** Delete the certbot step outright — obtaining a TLS certificate is not a `ghr-pypi` task. Delete the content-negotiation step and link `json-api` from `Where to go next` instead. The optional-password step is absorbed: in redirect mode authentication is load-bearing from the first request.

- [ ] **Step 7: `changelog.rst`.** One bullet: the tutorials assume an existing release process and cover four deployments, including the two redirect setups.

- [ ] **Step 7a: Generalize `tutorials/index.rst`'s shared premise — this task is the first point at which it becomes true.**

  Task 1 rewrote the landing page to say every tutorial starts from a repository already publishing wheels, which was false while `cloudflare.rst` and `nginx.rst` still opened with "Create the package". Task 2 narrowed the wording to whatever was accurate at that commit. **You are the task that makes it true of all four**, so widen it back — and check the neighbouring sentence too, since nginx additionally needs a server the reader can `sudo` on and a DNS name they control, which is more than "your host".

  Read what Task 2 left rather than assuming either earlier wording, and make the claim match the four tutorials as they now stand.

  **Also check "Everything you need to type is on the page."** Task 3's review flagged it as no longer holding for the Cloudflare tutorial, whose webhook step needs a deploy workflow the page describes but may not supply in full. Judge it against all four as they finally stand — if it is false for any of them, fix the sentence rather than the tutorials, since this is the last task and nobody follows you.

- [ ] **Step 8: Full gate.**

```
just fix
just test
just test-worker
just check-all > /tmp/gate.log 2>&1; echo EXIT=$?
uv run --no-default-groups --group docs sphinx-build -b html -a -E -n ./doc/source /tmp/t4
wc -l doc/source/tutorials/*.rst
```

`EXIT=0`, a warning-free strict build, 449 Python tests, 81 worker tests. Then:

```bash
grep -rn "certbot\|content negotiation\|What Workers would add" doc/source/tutorials/
grep -rn "tutorial-" doc/source --include='*.rst' | grep -v "tutorials/"
git status --short
```

The first grep should be empty. The second finds every cross-reference into the tutorials from elsewhere in the docs — **judge each one**, since a reference to a step that no longer exists still builds cleanly if it points at the page rather than the section. Report all three.

*(Driver checkpoint: commit as "Rewrite the nginx tutorial around the redirector")*

---

## After the plan

Driver: commit the four checkpoints. This closes the four-part deployment
programme. The remaining `direction.md` item is `_redirects` alongside
`_headers`.
