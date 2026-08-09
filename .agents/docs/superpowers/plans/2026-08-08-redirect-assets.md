# Redirect Asset Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **PROJECT RULE (AGENTS.md):** agents never commit or push — the human driver
> does. Implementers stop at "verified in working tree".

**Goal:** `assets: redirect` serves private release assets through a token-holding Cloudflare Worker, with Basic auth, without mirroring a single wheel.

**Architecture:** The build rewrites every file URL to `../../_assets/<asset-id>/<filename>` and emits a manifest listing exactly which assets are published. A shipped (not generated) `_worker.js` authenticates, checks the manifest, calls GitHub's asset API with a stored token, and returns the 302 to a signed URL. Because the Worker holds no build data, it is deployed once and every release is an ordinary Pages deploy.

**Spec:** `.agents/docs/superpowers/specs/2026-08-08-redirect-assets-design.md` (sub-project 2 of 4)

**Context for the implementer:**
- Current suite: 334 tests green, `just check-all` exit 0.
- **NEVER run `git checkout` on a source file.** Much of the tree is uncommitted; it would discard work. Revert with `Edit`.
- **Do NOT run `just test-all <path>`.** It destroys the project venv.
- **Leave nothing in the repo root.** Generated artifacts landed there twice in the previous sub-project. Check `git status --short` before finishing.
- `config.py` must not import `index` or `targets`.
- Node is available (`v24`), so `node --test` works without any new dependency.
- Read the files you are changing first; the code wins over this plan.

---

### Task 1: `assets: redirect` and `missing_metadata`

**Goal:** Both config keys validate; `missing_metadata` outside redirect mode is an error.

**Files:** Modify `src/ghr_pypi/config.py`, `tests/test_config.py`

**Acceptance Criteria:**
- [ ] `AssetMode` is `Literal["link", "mirror", "redirect"]`; `assets: redirect` loads
- [ ] The invalid-`assets` message now lists all three modes
- [ ] `MissingMetadata = Literal["extract", "warn"]`; `Config.missing_metadata` defaults to `"extract"`
- [ ] An invalid `missing_metadata` value raises listing the valid ones
- [ ] `missing_metadata` set with `assets` other than `redirect` raises
- [ ] `missing_metadata` **unset** with any mode is fine
- [ ] The `mirror: true` alias is unaffected — it still maps to `mirror`/`link` only

**Verify:** `uv run pytest tests/test_config.py -q`; `just check-types` clean

**Steps:**

- [ ] **Step 1: Failing tests** in `tests/test_config.py`, reusing its config-writing helper:

```python
def test_assets_redirect_loads(tmp_path):
    cfg = load(write(tmp_path, "repositories: [a/b]\nassets: redirect\n"))
    assert cfg.assets == "redirect"


def test_assets_error_lists_all_three_modes(tmp_path):
    with pytest.raises(ConfigError, match="must be one of link, mirror, redirect"):
        load(write(tmp_path, "repositories: [a/b]\nassets: sideways\n"))


def test_missing_metadata_defaults_to_extract(tmp_path):
    assert load(write(tmp_path, "repositories: [a/b]\n")).missing_metadata == "extract"


def test_missing_metadata_warn_loads(tmp_path):
    cfg = load(
        write(tmp_path, "repositories: [a/b]\nassets: redirect\nmissing_metadata: warn\n")
    )
    assert cfg.missing_metadata == "warn"


def test_missing_metadata_rejects_an_unknown_value(tmp_path):
    with pytest.raises(ConfigError, match="'missing_metadata' must be one of extract, warn"):
        load(
            write(tmp_path, "repositories: [a/b]\nassets: redirect\nmissing_metadata: nope\n")
        )


@pytest.mark.parametrize("mode", ["link", "mirror"])
def test_missing_metadata_outside_redirect_mode_is_rejected(tmp_path, mode):
    with pytest.raises(ConfigError, match="only applies when 'assets' is redirect"):
        load(
            write(tmp_path, f"repositories: [a/b]\nassets: {mode}\nmissing_metadata: warn\n")
        )
```

- [ ] **Step 2: Confirm they fail.** `uv run pytest tests/test_config.py -q`

- [ ] **Step 3: Implement.** In `src/ghr_pypi/config.py`:

```python
AssetMode = Literal["link", "mirror", "redirect"]
MissingMetadata = Literal["extract", "warn"]
```

Add `"missing_metadata"` to `_KNOWN_KEYS` and `missing_metadata: MissingMetadata = "extract"` to `Config`. Widen the `assets` membership check to `("link", "mirror", "redirect")` and its message to `must be one of link, mirror, redirect`. Then, **after** the `assets` block so it can see the resolved mode:

```python
    missing_metadata = raw.get("missing_metadata", "extract")
    if missing_metadata not in ("extract", "warn"):
        raise ConfigError(
            f"{path}: 'missing_metadata' must be one of extract, warn, "
            f"got {missing_metadata!r}"
        )
    if "missing_metadata" in raw and assets != "redirect":
        raise ConfigError(
            f"{path}: 'missing_metadata' only applies when 'assets' is redirect"
        )
```

Use `"missing_metadata" in raw`, not `raw.get(...) is not None` — the previous sub-project shipped a bug from exactly that substitution. Pass `missing_metadata=missing_metadata` into `Config(...)`.

- [ ] **Step 4: Verify.** `just test` (report the count); `just fix`; `just check-types`.

- [ ] **Step 5: Mutation.** Change `"missing_metadata" in raw` to `raw.get("missing_metadata") is not None` → confirm a test still catches the outside-redirect case with an explicit null. If none does, add one. Report.

*(Driver checkpoint: commit as "Add the redirect asset mode and missing_metadata")*

---

### Task 2: The redirect data path in `index.py`

**Goal:** Sidecar identity on `FileEntry`, URL rewriting, the manifest, and the extraction fallback.

**Files:** Modify `src/ghr_pypi/index.py`, `tests/test_index.py`

**Acceptance Criteria:**
- [ ] `FileEntry` gains `metadata_api_url: str`, empty when the release has no sidecar
- [ ] `redirect_urls(projects)` rewrites every `url` to `../../_assets/<id>/<filename>`
- [ ] It raises `RedirectError` for an entry with no `api_url`
- [ ] `write_manifest(projects, out_dir)` writes `_assets/manifest.json` with `repo`, `filename`, and `metadata_id` only when a sidecar exists
- [ ] `extract_missing_metadata(...)` downloads only wheels lacking a sidecar, writes `_assets/<id>/<filename>.metadata`, and sets `core_metadata` to its sha256
- [ ] Nothing changes for `link` or `mirror` mode

**Verify:** `uv run pytest tests/test_index.py -q`; `just check-types` clean

**Steps:**

- [ ] **Step 1: Failing tests** in `tests/test_index.py`. Reuse the file's existing fixtures and `paged_opener`-style fakes; read them first.

```python
def test_collect_records_the_sidecar_api_url():
    releases = [
        {
            "tag_name": "v1",
            "assets": [
                {
                    "name": "demo-1.0-py3-none-any.whl",
                    "browser_download_url": "https://example/demo-1.0-py3-none-any.whl",
                    "url": "https://api.github.com/repos/o/r/releases/assets/11",
                    "digest": "sha256:" + "a" * 64,
                },
                {
                    "name": "demo-1.0-py3-none-any.whl.metadata",
                    "browser_download_url": "https://example/demo.metadata",
                    "url": "https://api.github.com/repos/o/r/releases/assets/12",
                    "digest": "sha256:" + "b" * 64,
                },
            ],
        }
    ]
    entry = index.collect_projects(releases)["demo"][0]
    assert entry["metadata_api_url"].endswith("/12")
    assert entry["core_metadata"] == "b" * 64


def test_redirect_urls_rewrites_every_entry(tmp_path):
    projects = {
        "demo": [
            {
                "filename": "demo-1.0-py3-none-any.whl",
                "url": "https://example/x",
                "api_url": "https://api.github.com/repos/o/r/releases/assets/11",
                "metadata_api_url": "",
                "sha256": None, "size": 0, "upload_time": None,
                "core_metadata": False, "source_repo": "o/r", "yanked": False,
            }
        ]
    }
    index.redirect_urls(projects)
    assert projects["demo"][0]["url"] == "../../_assets/11/demo-1.0-py3-none-any.whl"


def test_redirect_urls_rejects_an_entry_without_an_api_url():
    projects = {"demo": [{"filename": "d.whl", "url": "", "api_url": "",
                          "metadata_api_url": "", "sha256": None, "size": 0,
                          "upload_time": None, "core_metadata": False,
                          "source_repo": "o/r", "yanked": False}]}
    with pytest.raises(index.RedirectError, match="no asset API URL"):
        index.redirect_urls(projects)
```

Plus manifest tests asserting `version`, the `repo`/`filename` fields, `metadata_id` present for a paired wheel and **absent** for an unpaired one; and extraction tests asserting a wheel *with* a sidecar is never downloaded, one *without* is downloaded once and its `.metadata` written at the right path with `core_metadata` set to the digest. Build the fake wheels with `zipfile` as `tests/test_index.py` already does for `read_wheel_metadata`.

- [ ] **Step 2: Confirm they fail.**

- [ ] **Step 3: `FileEntry` and pairing.** Add `metadata_api_url: str` to `FileEntry` with a docstring line ("the sidecar's asset API endpoint, empty when the release has none; internal, not emitted"). Change the pairing dict to hold the whole asset:

```python
        metadata_assets: dict[str, dict[str, Any]] = {}
        if metadata:
            for asset in release.get("assets", []):
                name = asset["name"]
                if not name.endswith(".metadata") or _unsafe_name(name):
                    continue
                metadata_assets[name[: -len(".metadata")]] = asset
```

and the consumption:

```python
            core_metadata: str | bool = False
            metadata_api_url = ""
            if name.endswith(".whl") and name in metadata_assets:
                sidecar = metadata_assets[name]
                core_metadata = _sha256_digest(sidecar) or True
                metadata_api_url = sidecar.get("url") or ""
```

Add `"metadata_api_url": metadata_api_url,` to the appended entry.

- [ ] **Step 4: The redirect functions.** Add beside `MirrorError`:

```python
class RedirectError(RuntimeError):
    """Raised when an entry cannot be served through the redirector."""


def _asset_id(api_url: str) -> str:
    """Return the trailing asset id of a GitHub asset API URL."""
    return api_url.rstrip("/").rsplit("/", 1)[-1]


def redirect_urls(projects: Projects) -> None:
    """Point every entry at the site's own redirector path.

    ``../../_assets/<id>/<filename>`` is relative to ``simple/<project>/``, so
    the site can be served from any prefix — the property mirroring relies on
    too.
    """
    for files in projects.values():
        for entry in files:
            if not entry["api_url"]:
                raise RedirectError(
                    f"{entry['filename']} has no asset API URL; "
                    "redirect mode cannot serve it"
                )
            entry["url"] = (
                f"../../_assets/{_asset_id(entry['api_url'])}/{entry['filename']}"
            )


def write_manifest(projects: Projects, out_dir: Path) -> Path:
    """Write the redirector's allow-list of published assets.

    The redirector serves only what this lists, so a leaked index URL cannot be
    turned into a fetch of any asset the token happens to be able to read.
    """
    assets: dict[str, dict[str, Any]] = {}
    for files in projects.values():
        for entry in files:
            record: dict[str, Any] = {
                "repo": entry["source_repo"],
                "filename": entry["filename"],
            }
            if entry["metadata_api_url"]:
                record["metadata_id"] = _asset_id(entry["metadata_api_url"])
            assets[_asset_id(entry["api_url"])] = record
    target = out_dir / "_assets" / "manifest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"version": 1, "assets": assets}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target
```

- [ ] **Step 5: `extract_missing_metadata`.** Add below them:

```python
def extract_missing_metadata(
    projects: Projects,
    out_dir: Path,
    token: str,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> None:
    """Write ``.metadata`` for wheels whose release carries no sidecar.

    Redirect mode serves ``<file-url>.metadata`` from a path we own, so the
    sidecar can be an ordinary static file. Each such wheel is downloaded once
    to a temporary file, read, and discarded — the one cost redirect mode pays
    over link mode.

    A truncated download is not checked separately: it fails the zip read,
    which is already handled and warned about.
    """
    for files in projects.values():
        for entry in files:
            if not entry["filename"].endswith(".whl") or entry["metadata_api_url"]:
                continue
            request = urllib.request.Request(
                entry["api_url"],
                headers={
                    "Accept": "application/octet-stream",
                    "Authorization": f"Bearer {token}",
                },
            )
            with tempfile.NamedTemporaryFile(suffix=".whl", delete=False) as wheel:
                temp = Path(wheel.name)
                try:
                    with opener(  # nosec B310 — https API URL from the release payload
                        request, timeout=60
                    ) as response:
                        for chunk in iter(lambda: response.read(65536), b""):
                            wheel.write(chunk)
                    wheel.flush()
                    payload = read_wheel_metadata(temp)
                except (OSError, zipfile.BadZipFile) as error:
                    print(
                        f"warning: cannot extract metadata from "
                        f"{entry['filename']}: {error}",
                        file=sys.stderr,
                    )
                    continue
                finally:
                    temp.unlink(missing_ok=True)
            sidecar = (
                out_dir / "_assets" / _asset_id(entry["api_url"])
                / f"{entry['filename']}.metadata"
            )
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_bytes(payload)
            entry["core_metadata"] = hashlib.sha256(payload).hexdigest()
```

Add `import tempfile` if absent. **Check the `finally`/`continue` interaction carefully** — the temp file must be removed on every path, including the success path, and `continue` inside `try` still runs `finally`.

- [ ] **Step 6: Verify.** `just test` (report the count); `just fix`; `just check-types`.

- [ ] **Step 7: Mutations.** One at a time, reverting with `Edit`:
  1. Drop the `or entry["metadata_api_url"]` guard so paired wheels are downloaded too → confirm a test fails.
  2. Always write `metadata_id` even when empty → confirm a manifest test fails.
  3. Change `../../_assets/` to `/_assets/` → confirm a URL test fails.

  Report all three.

*(Driver checkpoint: commit as "Add the redirect data path")*

---

### Task 3: The Worker and its node tests

**Goal:** A reviewable, tested `_worker.js`.

**Files:** Create `src/ghr_pypi/targets/_worker.js`, `tests/worker/worker.test.mjs`; modify `justfile`, `pyproject.toml`

**Acceptance Criteria:**
- [ ] `_worker.js` is a real file in the package and is included in the wheel
- [ ] It authenticates every request, delegating non-asset paths to `env.ASSETS`
- [ ] Unknown ID, or filename mismatch, → 404
- [ ] `.metadata` with `metadata_id` → 302; without → delegated to `env.ASSETS`
- [ ] The token never appears in a response
- [ ] `just test-worker` runs the node suite and **skips cleanly when node is absent**

**Verify:** `just test-worker` → all pass

**Steps:**

- [ ] **Step 1: Write `src/ghr_pypi/targets/_worker.js`.**

```js
/**
 * ghr-pypi redirector for Cloudflare Pages (advanced mode).
 *
 * Every request reaches this Worker first, so authentication here gates the
 * whole site — index pages included. Anything that is not an asset request is
 * handed to the static assets binding.
 *
 * It holds no build data: the manifest is fetched from the deployed site, so
 * a release only needs a Pages deploy, never a Worker redeploy.
 *
 * Secrets: GHR_PYPI_USER, GHR_PYPI_PASSWORD, GITHUB_TOKEN.
 */

const ASSET_PREFIX = "/_assets/";
const MANIFEST_PATH = "/_assets/manifest.json";
const CACHE_SECONDS = 60;
const REALM = 'Basic realm="ghr-pypi", charset="UTF-8"';

function unauthorized() {
  // pip consults netrc/keyring only when it sees WWW-Authenticate.
  return new Response("Unauthorized\n", {
    status: 401,
    headers: { "WWW-Authenticate": REALM },
  });
}

function constantTimeEqual(a, b) {
  const encoder = new TextEncoder();
  const left = encoder.encode(a);
  const right = encoder.encode(b);
  // Length is not secret; the comparison below is what must not short-circuit.
  if (left.length !== right.length) return false;
  let diff = 0;
  for (let i = 0; i < left.length; i++) diff |= left[i] ^ right[i];
  return diff === 0;
}

function authorized(request, env) {
  const header = request.headers.get("Authorization") || "";
  if (!header.startsWith("Basic ")) return false;
  let decoded;
  try {
    decoded = atob(header.slice(6));
  } catch {
    return false;
  }
  const separator = decoded.indexOf(":");
  if (separator < 0) return false;
  // Both are compared so a wrong user costs the same as a wrong password.
  const userOk = constantTimeEqual(decoded.slice(0, separator), env.GHR_PYPI_USER || "");
  const passOk = constantTimeEqual(decoded.slice(separator + 1), env.GHR_PYPI_PASSWORD || "");
  return userOk && passOk;
}

async function loadManifest(request, env) {
  const url = new URL(request.url);
  url.pathname = MANIFEST_PATH;
  const response = await env.ASSETS.fetch(new Request(url, { method: "GET" }));
  if (!response.ok) return null;
  return response.json();
}

export default {
  async fetch(request, env) {
    if (!authorized(request, env)) return unauthorized();

    const url = new URL(request.url);
    if (!url.pathname.startsWith(ASSET_PREFIX) || url.pathname === MANIFEST_PATH) {
      return env.ASSETS.fetch(request);
    }

    const rest = url.pathname.slice(ASSET_PREFIX.length);
    const slash = rest.indexOf("/");
    if (slash < 0) return env.ASSETS.fetch(request);
    const id = rest.slice(0, slash);
    let name = decodeURIComponent(rest.slice(slash + 1));

    const manifest = await loadManifest(request, env);
    const entry = manifest && manifest.assets ? manifest.assets[id] : null;
    if (!entry) return new Response("Not found\n", { status: 404 });

    let assetId = id;
    if (name.endsWith(".metadata")) {
      if (!entry.metadata_id) {
        // No sidecar release asset: the build wrote a static file here.
        return env.ASSETS.fetch(request);
      }
      if (name.slice(0, -".metadata".length) !== entry.filename) {
        return new Response("Not found\n", { status: 404 });
      }
      assetId = entry.metadata_id;
    } else if (name !== entry.filename) {
      // A listed id must not be fetchable under an arbitrary name.
      return new Response("Not found\n", { status: 404 });
    }

    const cacheKey = new Request(`${url.origin}${ASSET_PREFIX}${assetId}`, { method: "GET" });
    const cache = caches.default;
    const cached = await cache.match(cacheKey);
    if (cached) return cached;

    const api = `https://api.github.com/repos/${entry.repo}/releases/assets/${assetId}`;
    const upstream = await fetch(api, {
      redirect: "manual",
      headers: {
        Accept: "application/octet-stream",
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        "User-Agent": "ghr-pypi",
      },
    });
    const location = upstream.headers.get("Location");
    if (upstream.status !== 302 || !location) {
      // Never surface GitHub's body or headers: they can carry token detail.
      return new Response("Upstream error\n", { status: 502 });
    }
    const redirect = new Response(null, {
      status: 302,
      headers: {
        Location: location,
        "Cache-Control": `private, max-age=${CACHE_SECONDS}`,
      },
    });
    await cache.put(cacheKey, redirect.clone());
    return redirect;
  },
};
```

- [ ] **Step 2: Ship it in the wheel.** `_worker.js` must be package data. Check how `pyproject.toml` includes `src/ghr_pypi/templates/*.html` and follow the same mechanism; if hatchling already includes all non-Python files under the package, say so and add nothing.

- [ ] **Step 3: Write `tests/worker/worker.test.mjs`** using `node:test` and `node:assert`. Stub `env.ASSETS.fetch` to serve a fixed manifest and a marker response, and stub global `fetch` to return a 302. Cover exactly the acceptance criteria plus: no credentials → 401 **with** `WWW-Authenticate`; wrong credentials → 401; unknown id → 404; filename mismatch → 404; happy path → 302 with the signed Location; `.metadata` with `metadata_id` → 302 to the sidecar id; `.metadata` without → delegated (assert the marker response, **not** a 404); `/simple/foo/` → delegated; the token appears in no response header or body on any path; and a second request inside the cache window makes no second upstream call.

  The harness shape is the risky part, so start from this and add the cases:

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import worker from "../../src/ghr_pypi/targets/_worker.js";

const MANIFEST = {
  version: 1,
  assets: { "11": { repo: "o/r", filename: "demo-1.0-py3-none-any.whl", metadata_id: "12" },
            "21": { repo: "o/r", filename: "solo-1.0-py3-none-any.whl" } },
};
const CREDS = "Basic " + Buffer.from("u:p").toString("base64");

function makeEnv() {
  return {
    GHR_PYPI_USER: "u", GHR_PYPI_PASSWORD: "p", GITHUB_TOKEN: "secret-token",
    ASSETS: {
      fetch: async (request) =>
        new URL(request.url).pathname === "/_assets/manifest.json"
          ? new Response(JSON.stringify(MANIFEST), { status: 200 })
          : new Response("STATIC", { status: 200 }),
    },
  };
}

function installStubs() {
  const store = new Map();
  globalThis.caches = {
    default: {
      match: async (key) => store.get(key.url)?.clone(),
      put: async (key, value) => void store.set(key.url, value),
    },
  };
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return new Response(null, { status: 302, headers: { Location: "https://signed/x" } });
  };
  return { upstreamCalls: () => calls };
}

const get = (path, headers = {}) =>
  new Request(`https://pypi.example.com${path}`, { headers });

test("no credentials asks for them", async () => {
  const response = await worker.fetch(get("/simple/"), makeEnv());
  assert.equal(response.status, 401);
  assert.match(response.headers.get("WWW-Authenticate") || "", /^Basic /);
});
```

`caches` and `fetch` are globals the Worker reads, so `installStubs()` must run
before each test that needs them; reset between tests rather than sharing state.

- [ ] **Step 4: `just test-worker`.**

```just
# run the Cloudflare Worker test suite (skipped when node is unavailable)
test-worker:
    @command -v node >/dev/null 2>&1 \
      && node --test tests/worker/ \
      || echo "node not found — skipping worker tests"
```

Wire it into `check-all` **only if** it can skip without failing; verify by temporarily shadowing `node` on `PATH`. Report what you did.

- [ ] **Step 5: Verify.** `just test-worker` → pass. `just test` → unchanged count. `just fix`; `just check-types`.

- [ ] **Step 6: Mutations.** Reverting each with `Edit`:
  1. Remove the `WWW-Authenticate` header → confirm a test fails.
  2. Make `constantTimeEqual` return `true` when lengths differ → confirm a test fails.
  3. Remove the filename check → confirm a test fails.
  4. Return `upstream` directly instead of a fresh `Response` → confirm the token-leak test fails.

  Report all four.

*(Driver checkpoint: commit as "Add the redirector Worker and its tests")*

---

### Task 4: Cloudflare target, gating, CLI wiring

**Goal:** A redirect build emits everything and refuses targets that cannot serve it.

**Files:** Modify `src/ghr_pypi/targets/cloudflare.py`, `src/ghr_pypi/cli.py`, `tests/test_targets.py`, `tests/test_cli.py`

**Acceptance Criteria:**
- [ ] `CloudflareTarget.supports_redirect = True`; `static` and `nginx` do not define it
- [ ] Under `assets: redirect` the target writes `_worker.js` (byte-identical to the packaged file) into `out_dir`, and `wrangler.toml` + `SETUP.md` into `target_dir`
- [ ] Under other modes it writes neither
- [ ] `assets: redirect` with a target lacking `supports_redirect` exits 1 naming the targets that qualify, **before the build**
- [ ] The CLI calls `extract_missing_metadata` (unless `missing_metadata: warn`), then `redirect_urls`, then `write_manifest`
- [ ] Deleting any of those three calls fails a test

**Verify:** `just test` → all pass (report the count)

**Steps:**

- [ ] **Step 1: Failing tests** in `tests/test_targets.py` and `tests/test_cli.py`, following the existing `context()` and `_target_run` helpers. **Route every CLI target test through `_target_run`** so nothing writes to the repo root.

- [ ] **Step 2: `cloudflare.py`.** Add `supports_redirect = True`. Under `site.assets == "redirect"`, copy the packaged `_worker.js` with `importlib.resources.files("ghr_pypi.targets").joinpath("_worker.js").read_text()` into `out_dir/_worker.js`, and write `wrangler.toml` and `SETUP.md` into `target_dir`. `SETUP.md` gives the three `wrangler secret put` commands (`GHR_PYPI_USER`, `GHR_PYPI_PASSWORD`, `GITHUB_TOKEN`) and the deploy line. Return every path written — the protocol requires a materialized `Sequence[Path]`.

- [ ] **Step 3: Gating in `cli.py`,** immediately after `get_target` succeeds and before any network call. `cli.py` currently imports `SiteContext, get_target` from `ghr_pypi.targets` — add `available_targets` to that import:

```python
    if cfg.assets == "redirect" and not getattr(selected, "supports_redirect", False):
        qualified = sorted(
            name
            for name, candidate in available_targets().items()
            if getattr(candidate, "supports_redirect", False)
        )
        typer.echo(
            f"error: target {cfg.target!r} cannot serve 'assets: redirect'; "
            f"targets that can: {', '.join(qualified)}",
            err=True,
        )
        raise typer.Exit(1)
```

- [ ] **Step 4: The build steps in `cli.py`,** after `collect_projects` and before `write_site`:

```python
    if cfg.assets == "redirect":
        if cfg.missing_metadata == "extract" and cfg.metadata:
            index.extract_missing_metadata(projects, out, token)
        index.redirect_urls(projects)
        index.write_manifest(projects, out)
```

Wrap it in the same `except (urllib.error.URLError, index.RedirectError)` shape the surrounding code uses, echoing `error: ...` and exiting 1. Note `extract_missing_metadata` must run **before** `redirect_urls`, because it reads `api_url` — which `redirect_urls` leaves alone, but the ordering should be explicit and commented.

Under `missing_metadata: warn`, emit the existing per-repository coverage warning instead, reusing `index.metadata_coverage` exactly as link mode does.

- [ ] **Step 5: Verify.** `just test` (report the count); `just fix`; `just check-types`.

- [ ] **Step 6: Mutations.** Reverting each with `Edit`:
  1. Delete the `write_manifest` call → confirm a test fails.
  2. Delete the `redirect_urls` call → confirm a test fails.
  3. Delete the gating block → confirm a test fails.
  4. Change `_worker.js`'s destination to `target_dir` → confirm a test fails (Pages reads it from the site root; in `target_dir` it would never deploy).

  Report all four.

*(Driver checkpoint: commit as "Wire redirect mode into the Cloudflare target")*

---

### Task 5: Docs and full gate

**Goal:** The mode, its security properties and its setup are documented; the gate is green.

**Files:** Modify `doc/source/reference/{configuration,cli,targets}.rst`, `doc/source/how-to/index.rst`, `doc/source/changelog.rst`, `README.md`, `direction.md`; create `doc/source/how-to/private-packages-without-mirroring.rst`

**Acceptance Criteria:**
- [ ] `configuration.rst` documents `assets: redirect` and `missing_metadata`, with every new error verbatim from the source
- [ ] `cli.rst` documents the new exit-1 conditions verbatim
- [ ] `targets.rst` documents `supports_redirect` as an optional protocol attribute
- [ ] The new how-to covers the config, `wrangler deploy`, the three secrets, and the pip side (netrc or credentials in the index URL)
- [ ] The security notes state what the manifest reveals, why the index is gated too, and that Cloudflare Access is **not** a substitute because pip cannot send its headers
- [ ] `just check-all` → exit 0 and a warning-free strict HTML build

**Verify:** `just check-all > /tmp/gate.log 2>&1; echo EXIT=$?` → `EXIT=0`

**Steps:**

- [ ] **Step 1: `configuration.rst`.** Extend the `assets` section with `redirect`, stating that it needs a target providing a redirector and that it is the only mode where PEP 658 works for private repositories without mirroring. Add a `missing_metadata` section: `extract` (default) downloads each sidecar-less wheel once; `warn` emits the per-repository warning instead; setting it outside redirect mode is an error, and why it cannot take effect elsewhere. Update the summary table and the annotated example. Add every new message verbatim from `src/ghr_pypi/config.py`.

- [ ] **Step 2: `cli.rst`.** Add the new exit-1 conditions verbatim from `src/ghr_pypi/cli.py` and `index.py` — the `cannot serve 'assets: redirect'` error and the `RedirectError` text — in the order the code raises them.

- [ ] **Step 3: `targets.rst`.** Document `supports_redirect`: optional, read with `getattr(..., False)` so targets predating it keep working, and what a target must provide to set it.

- [ ] **Step 4: Create `doc/source/how-to/private-packages-without-mirroring.rst`,** titled as a question like its siblings. The config, `--target cloudflare`, `wrangler deploy`, the three `wrangler secret put` commands, and the pip side. State plainly that **Cloudflare Access cannot be used for this** — pip cannot send `CF-Access-Client-Id`/`Secret`, which is the whole reason the Worker does Basic auth. Note what the manifest reveals. Add it to the how-to toctree.

- [ ] **Step 5: `README.md`** — one line in the feature list and a short section, keeping lines short (that region is MyST-included). **Step 6: `changelog.rst`** — bullets for the mode and the key. **Step 7: `direction.md`** — narrow the remaining "target-specific artifact generation" item to the webhook receiver and the nginx redirector; the Worker redirector is done.

- [ ] **Step 8: Gate.**

```
just fix
just test
just test-worker
just check-all > /tmp/gate.log 2>&1; echo EXIT=$?
uv run --no-default-groups --group docs sphinx-build -b html -a -E -n ./doc/source /tmp/ra-docs
```

`EXIT=0` and a warning-free strict build. Then confirm nothing stale survives and nothing leaked:

```bash
grep -rn "assets: redirect\|missing_metadata\|supports_redirect" --include='*.rst' --include='*.md' doc/source README.md
git status --short
```

Report both, and judge every hit.

*(Driver checkpoint: commit as "Document redirect mode")*

---

## After the plan

Driver: commit the checkpoints. Sub-project 3 is webhook rebuilds; sub-project 4
is the tutorial rewrite, including the Cloudflare tutorial reorientation around
this secured setup.
