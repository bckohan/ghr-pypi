# Worker Tests in CI and Codecov Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **PROJECT RULE (AGENTS.md):** agents never commit or push — the human driver
> does. Implementers stop at "verified in working tree".

**Goal:** The Cloudflare Worker's 51 tests run in CI, block releases, and report their coverage to Codecov under their own flag.

**Architecture:** `just test-worker` gains Node's coverage reporter and a threshold floor, so the command CI runs is the command a developer runs. A dedicated `test-worker` job runs it once, uploads `worker.lcov`, and `coverage-combine` sends it to Codecov as a second upload with `flags: worker`.

**Spec:** `.agents/docs/superpowers/specs/2026-08-09-worker-ci-coverage-design.md`

**Context for the implementer:**
- Current: 391 Python tests, 51 worker tests, `just check-all` exit 0.
- **NEVER run `git checkout`** — much of the tree is uncommitted. Revert with `Edit`.
- **Do NOT run `just test-all <path>`** — it destroys the project venv.
- **Leave nothing untracked in the repo root** beyond what is gitignored.
- Node 24.2 is installed locally.
- Read `AGENTS.md` at the repo root before starting.

---

### Task 1: Coverage and thresholds in `just test-worker`

**Goal:** `just test-worker` writes `worker.lcov` and fails on a coverage regression, while keeping its skip and failure-propagation behaviour.

**Files:** Modify `justfile`, `.gitignore`

**Acceptance Criteria:**
- [ ] `just test-worker` writes `worker.lcov` containing `SF:src/ghr_pypi/targets/_worker.js`
- [ ] Human-readable test output still appears on stdout
- [ ] A raised threshold fails the run; a lowered one passes — proving the flags take effect
- [ ] A failing worker test still makes the recipe exit non-zero
- [ ] Node absent on `PATH` still skips cleanly with exit 0
- [ ] Both the `[unix]` and `[windows]` variants carry the same flags
- [ ] `worker.lcov` is gitignored

**Verify:** `just test-worker` → 51 pass and `worker.lcov` written; `just check-all` → exit 0

**Steps:**

- [ ] **Step 1: `justfile`.** Replace both recipes. The flags are identical in each; only the shell differs.

```just
# run the Cloudflare Worker test suite (skipped when node is unavailable)
# Both reporters are named deliberately: --test-reporter REPLACES the default,
# so listing only lcov would leave CI with no readable output.
[unix]
test-worker:
    @if command -v node >/dev/null 2>&1; then \
        node --test --experimental-test-coverage \
            --test-coverage-lines=95 --test-coverage-branches=90 \
            --test-reporter=spec --test-reporter-destination=stdout \
            --test-reporter=lcov --test-reporter-destination=worker.lcov \
            "tests/worker/**/*.test.mjs"; \
    else \
        echo "node not found - skipping worker tests"; \
    fi

# run the Cloudflare Worker test suite (skipped when node is unavailable)
[windows]
test-worker:
    @if (Get-Command node -ErrorAction SilentlyContinue) { node --test --experimental-test-coverage --test-coverage-lines=95 --test-coverage-branches=90 --test-reporter=spec --test-reporter-destination=stdout --test-reporter=lcov --test-reporter-destination=worker.lcov "tests/worker/**/*.test.mjs" } else { echo "node not found - skipping worker tests" }
```

**Keep the `if/else` shape.** A `cmd && test || echo "skipping"` chain turns a genuine failure into a green skip, which this project has already shipped once.

- [ ] **Step 2: `.gitignore`.** Add `worker.lcov` beside the existing `coverage.xml` entry, in the same "Unit test / coverage reports" block.

- [ ] **Step 3: Verify the flags take effect, do not assume it.**

```
just test-worker
grep -c '^SF:src/ghr_pypi/targets/_worker.js' worker.lcov     # expect 1
```

Then temporarily set `--test-coverage-lines=100` and confirm the recipe **fails**; restore with `Edit` and confirm it passes again. A threshold flag that is accepted and ignored would otherwise look identical to one that works. Report both observations.

- [ ] **Step 4: Re-prove the two behaviours the flags must not break.**
  1. Drop a deliberately failing test file into `tests/worker/`, run `just test-worker`, confirm **exit 1**, then delete it and confirm exit 0.
  2. Build a directory of symlinks containing `just` and a shell but **not** `node`, run `PATH=<that dir> just test-worker`, and confirm the skip message with **exit 0**.

  Report both, and confirm no stray test file is left behind.

- [ ] **Step 5:** `just check-all > /tmp/gate.log 2>&1; echo EXIT=$?` → `EXIT=0`. Confirm `git status --short` shows `worker.lcov` as **ignored**, not untracked.

*(Driver checkpoint: commit as "Collect Worker test coverage")*

---

### Task 2: The CI job and the Codecov upload

**Goal:** CI runs the worker tests once, they block releases, and their coverage reaches Codecov under `flags: worker`.

**Files:** Modify `.github/workflows/test.yml`

**Acceptance Criteria:**
- [ ] A `test-worker` job runs `just test-worker` on `ubuntu-latest` with Node 24
- [ ] It uploads `worker.lcov` under a name that does **not** match `*.coverage`
- [ ] `coverage-combine` lists `test-worker` in `needs`, downloads that artifact, and uploads it to Codecov with `flags: worker`
- [ ] The existing Python upload gains `flags: python` and is otherwise unchanged
- [ ] Every action is pinned by commit SHA, matching the file's existing style
- [ ] The workflow parses and, if `actionlint` is available, lints clean

**Verify:** the workflow parses; `just check-all` → exit 0

**Steps:**

- [ ] **Step 1: Add the job** to `.github/workflows/test.yml`, after `test-macos` and before `coverage-combine`. Copy the `actions/checkout` and `extractions/setup-just` SHAs verbatim from elsewhere in this file — do not invent them.

```yaml
  test-worker:
    name: Worker Tests
    runs-on: ubuntu-latest
    permissions:
      contents: read
    # The redirector is the auth boundary for a private index, so a failure here
    # must block a release. release.yml consumes this workflow with `uses:` and
    # its publish jobs declare `needs: test`, which succeeds only when EVERY job
    # here succeeds — so this job's presence is what makes it release-blocking.
    steps:
      - uses: actions/checkout@<same SHA as the other jobs>
        with:
          persist-credentials: false
      - uses: actions/setup-node@<pinned SHA>
        with:
          node-version: "24"
      - name: Setup Just
        uses: extractions/setup-just@<same SHA as the other jobs>
      - name: Run Worker Tests
        run: just test-worker
      - name: Store worker coverage
        uses: actions/upload-artifact@<same SHA as the other jobs>
        with:
          # deliberately not *.coverage: coverage-combine globs that pattern and
          # feeds everything it finds to `coverage combine`, which cannot read lcov
          name: worker-lcov
          path: worker.lcov
```

**You must pin `actions/setup-node` yourself.** Resolve the SHA for the current major release tag, for example:

```
gh api repos/actions/setup-node/git/ref/tags/v6 --jq .object.sha
```

If that returns an annotated-tag object rather than a commit, dereference it. Put the version in a trailing comment the way `actions/configure-pages@… # v6.0.0` does in `pages.yml`. Report the SHA you used and how you obtained it.

- [ ] **Step 2: Wire it into `coverage-combine`.** Add `test-worker` to `needs`. After the existing `- run: just coverage` step, add the download and split the upload into two:

```yaml
      - name: Get worker coverage
        uses: actions/download-artifact@<same SHA as the step above>
        with:
          name: worker-lcov
      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@fb8b3582c8e4def4969c97caa2f19720cb33a72f
        with:
          use_oidc: true
          files: ./coverage.xml
          flags: python
      - name: Upload worker coverage to Codecov
        uses: codecov/codecov-action@fb8b3582c8e4def4969c97caa2f19720cb33a72f
        with:
          use_oidc: true
          files: ./worker.lcov
          flags: worker
```

Two steps rather than one because `flags` applies to every file in a step, so a single step cannot label the two sources differently. `id-token: write` is already granted at the job level and covers both.

- [ ] **Step 3: Verify the file.** Run `actionlint` if it is available (`command -v actionlint`); if not, say so plainly rather than skipping silently, and parse the YAML instead:

```
uv run --no-project --with pyyaml python -c "import yaml,sys; d=yaml.safe_load(open('.github/workflows/test.yml')); print(sorted(d['jobs'])); print(d['jobs']['coverage-combine']['needs'])"
```

Confirm `test-worker` appears in both the job list and `coverage-combine`'s `needs`, and that every `uses:` in the file is SHA-pinned:

```
grep -n "uses:" .github/workflows/test.yml
```

- [ ] **Step 4:** `just check-all > /tmp/gate.log 2>&1; echo EXIT=$?` → `EXIT=0`; `just test` and `just test-worker` unchanged. Confirm `git status --short` and a clean repo root.

*(Driver checkpoint: commit as "Run the Worker tests in CI and report their coverage")*

---

## After the plan

Driver: commit both checkpoints. On the next push the `Worker Tests` job appears
in the Test workflow, and Codecov shows `python` and `worker` as separate flags —
the second will have no history until the first upload lands.
