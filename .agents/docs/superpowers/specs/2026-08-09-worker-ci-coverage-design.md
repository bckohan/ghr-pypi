# Worker Tests in CI and Codecov — Design

**Date:** 2026-08-09
**Status:** Approved

## Problem

`tests/worker/worker.test.mjs` covers the Cloudflare redirector — the auth
boundary for a private index — with 51 tests. It runs only when someone types
`just test-worker` locally. CI never executes it, its coverage appears nowhere,
and a release can publish a broken redirector.

## Verified mechanics

```
node --test --experimental-test-coverage \
     --test-reporter=spec --test-reporter-destination=stdout \
     --test-reporter=lcov --test-reporter-destination=worker.lcov \
     "tests/worker/**/*.test.mjs"
```

Executed on Node 24.2: emits `SF:src/ghr_pypi/targets/_worker.js` — repo-relative,
which is what Codecov needs — and Node already excludes the test file itself.
Current numbers are **99.37% lines, 98.02% branches**.

**Both reporters must be listed.** Specifying `--test-reporter` *replaces* the
default rather than adding to it, so naming only `lcov` would leave CI with no
human-readable output and failures would have to be read out of an lcov file.

## Decisions

### A dedicated `test-worker` job

The Worker is Python- and platform-independent. Folding it into the existing
3-OS × 2-resolution matrix would run identical JavaScript ten times for one
result, so it gets its own job with `actions/setup-node`, pinned to Node 24 to
match what the tests were written against.

**Releases are gated automatically.** `release.yml` consumes `test.yml` with
`uses:` and its publish jobs declare `needs: test`; a reusable workflow's
`needs` succeeds only when *every* job in it succeeds. No change to
`release.yml` — but that also means adding this job silently makes it
release-blocking, which is the intent and should be stated in the job's
comment so nobody removes it casually.

### Separate Codecov flags

`coverage.xml` and `worker.lcov` upload as two steps with `flags: python` and
`flags: worker`. `codecov-action`'s `flags` applies to every file in its step,
so one step cannot label two sources differently.

Merging them into a single number would be meaningless across languages and
actively harmful: a Worker regression from 99% to 60% is ~200 lines against
~1400 lines of Python, moving the total by a fraction of a percent. Flags keep
the auth boundary visible on its own.

The second step mirrors the existing one exactly — same action pin, same OIDC
configuration — differing only in `files` and `flags`. `id-token: write` is
already granted at the job level and covers both.

**The artifact must not be named to match `*.coverage`.** `coverage-combine`
downloads with `pattern: "*.coverage"` and feeds everything it finds to
`coverage combine`, which would choke on an lcov file. The Worker artifact is
named distinctly and downloaded in its own step.

### A threshold floor, not a pin

`--test-coverage-lines=95 --test-coverage-branches=90` — below today's
99.37/98.02, so a real regression fails while an honest refactor that briefly
dips does not. Pinning at today's exact numbers would fail on any refactor that
removes a covered line, which trains people to edit the threshold rather than
write the test.

Because the thresholds live in the `just` recipe, they fail at
`just test-worker` and `just check-all` locally — not only after a Codecov
comment.

### One command, run the same way everywhere

`just test-worker` gains the coverage flags, so the command CI runs is the
command a developer runs. That is the property `just check-all` already
maintains here, and it is why the lcov path is fixed rather than passed in.

`worker.lcov` is gitignored, alongside the existing `coverage.xml` entry.

The recipe must keep its **node-absent skip** and its **failure propagation**:
`command -v node && node --test … || echo "skipping"` silently turns a genuine
failure into a green skip, which is why the current recipe uses `if/else`. That
shape must survive the change — in CI node is always present, so a regression
there would be invisible until someone ran it locally.

## Files

| File | Change |
|---|---|
| `justfile` | `test-worker` gains coverage + threshold flags, both OS variants |
| `.gitignore` | `worker.lcov` |
| `.github/workflows/test.yml` | new `test-worker` job; artifact upload; second Codecov step in `coverage-combine` |

## Tests

This is CI configuration, so the verification is behavioural rather than a new
test file:

- `just test-worker` passes locally and writes `worker.lcov` with
  `SF:src/ghr_pypi/targets/_worker.js`.
- A deliberately failing worker test makes the recipe exit non-zero (the
  failure-propagation property, re-proven after the flags are added).
- Node absent on `PATH` still skips cleanly with exit 0.
- Lowering a threshold below current coverage passes; raising one above it
  fails — proving the flags are actually in effect rather than accepted and
  ignored.
- `just check-all` still exits 0.
- The workflow file parses: `actionlint` if available, otherwise a YAML parse
  plus a careful read.

## Out of scope

- Coverage for anything else in the repo.
- A Codecov `codecov.yml` with per-flag targets or carryforward — flags are
  created on first upload, and per-flag gates can be added once there is
  history to set them from.
- Running the Worker against a real Cloudflare deployment.
