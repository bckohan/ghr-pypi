import { test } from "node:test";
import assert from "node:assert/strict";
import worker from "../../src/ghr_pypi/targets/_worker.js";

const MANIFEST = {
  version: 1,
  assets: {
    11: { repo: "o/r", filename: "demo-1.0-py3-none-any.whl", metadata_id: "12" },
    21: { repo: "o/r", filename: "solo-1.0-py3-none-any.whl" },
  },
};
const CREDS = "Basic " + Buffer.from("u:p").toString("base64");
const TOKEN = "secret-token";
const PAT = "github_pat_client_fine_grained";
const SIGNED = "https://signed.example/x?token=abc123&expires=1";

function makeEnv(overrides = {}) {
  return {
    GHR_PYPI_USER: "u",
    GHR_PYPI_PASSWORD: "p",
    GHR_PYPI_TOKEN: TOKEN,
    ASSETS: {
      fetch: async (request) =>
        new URL(request.url).pathname === "/_assets/manifest.json"
          ? new Response(JSON.stringify(MANIFEST), { status: 200 })
          : new Response("STATIC", { status: 200 }),
    },
    ...overrides,
  };
}

/** An env whose manifest is exactly `assets`, for probing one guard at a time. */
function envWithAssets(assets) {
  return makeEnv({
    ASSETS: {
      fetch: async (request) =>
        new URL(request.url).pathname === "/_assets/manifest.json"
          ? new Response(JSON.stringify({ version: 1, assets }), { status: 200 })
          : new Response("STATIC", { status: 200 }),
    },
  });
}

const originalFetch = globalThis.fetch;

/**
 * `fetch` is a global the Worker reads. Each test installs its own stub so no
 * call count leaks between tests.
 */
function installStubs(t, options = {}) {
  const requests = [];
  globalThis.fetch = async (url, init) => {
    requests.push({ url, init });
    if (options.upstream) return options.upstream(url, init);
    // The stub answers the way GitHub does *and* echoes the credential back,
    // so any response the Worker forwards instead of rebuilding is caught by
    // the "token appears in no response" test rather than passing silently.
    const sent = (init && init.headers && init.headers.Authorization) || "";
    return new Response(`upstream body: ${sent}`, {
      status: 302,
      headers: { Location: SIGNED, "X-Upstream-Echo": sent },
    });
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  return { requests, upstreamCalls: () => requests.length };
}

const get = (path, headers = {}) => new Request(`https://pypi.example.com${path}`, { headers });

const auth = (path) => get(path, { Authorization: CREDS });

const basic = (user, password) =>
  "Basic " + Buffer.from(`${user}:${password}`, "utf8").toString("base64");

const WHEEL = "/_assets/11/demo-1.0-py3-none-any.whl";

// --- authentication -------------------------------------------------------

test("no credentials asks for them", async () => {
  const response = await worker.fetch(get("/simple/"), makeEnv());
  assert.equal(response.status, 401);
  assert.match(response.headers.get("WWW-Authenticate") || "", /^Basic /);
});

test("a same-length wrong password is refused", async () => {
  // Same length as "p", so the length pre-check cannot answer and the
  // constant-time comparison itself is what must reject it.
  const response = await worker.fetch(
    get("/simple/", { Authorization: basic("u", "x") }),
    makeEnv(),
  );
  assert.equal(response.status, 401);
});

test("a same-length wrong user is refused", async () => {
  const response = await worker.fetch(
    get("/simple/", { Authorization: basic("z", "p") }),
    makeEnv(),
  );
  assert.equal(response.status, 401);
});

test("a longer wrong password is refused", async () => {
  const response = await worker.fetch(
    get("/simple/", { Authorization: basic("u", "nope") }),
    makeEnv(),
  );
  assert.equal(response.status, 401);
});

test("a longer wrong user is refused", async () => {
  const response = await worker.fetch(
    get("/simple/", { Authorization: basic("nope", "p") }),
    makeEnv(),
  );
  assert.equal(response.status, 401);
});

test("a non-Basic scheme is refused", async () => {
  const response = await worker.fetch(
    get("/simple/", { Authorization: `Bearer ${TOKEN}` }),
    makeEnv(),
  );
  assert.equal(response.status, 401);
});

test("undecodable base64 is refused", async () => {
  const response = await worker.fetch(get("/simple/", { Authorization: "Basic !!!" }), makeEnv());
  assert.equal(response.status, 401);
});

test("a payload with no colon is refused", async () => {
  const payload = "Basic " + Buffer.from("nocolon").toString("base64");
  const response = await worker.fetch(get("/simple/", { Authorization: payload }), makeEnv());
  assert.equal(response.status, 401);
});

test("unset secrets do not become empty credentials", async () => {
  const env = makeEnv({ GHR_PYPI_USER: undefined, GHR_PYPI_PASSWORD: undefined });
  const response = await worker.fetch(
    get("/simple/", { Authorization: basic("", "") }),
    env,
  );
  assert.equal(response.status, 401);
});

test("an invalid utf-8 credential cannot stand in for U+FFFD", async (t) => {
  // A lenient decoder turns the lone byte 0x80 into U+FFFD, which would then
  // *match* a password that really is U+FFFD. Strict decoding is what makes
  // one raw byte fail to impersonate the replacement character.
  installStubs(t);
  const env = makeEnv({ GHR_PYPI_USER: "u", GHR_PYPI_PASSWORD: "�" });
  const raw = Buffer.concat([Buffer.from("u:", "utf8"), Buffer.from([0x80])]);
  const response = await worker.fetch(
    get("/simple/", { Authorization: "Basic " + raw.toString("base64") }),
    env,
  );
  assert.equal(response.status, 401);
  // The genuine credential still works, so this is strictness, not breakage.
  const ok = await worker.fetch(get("/simple/", { Authorization: basic("u", "�") }), env);
  assert.equal(ok.status, 200);
});

test("a non-ASCII credential authenticates", async (t) => {
  installStubs(t);
  const env = makeEnv({ GHR_PYPI_USER: "üser", GHR_PYPI_PASSWORD: "pÄss" });
  const ok = await worker.fetch(get("/simple/", { Authorization: basic("üser", "pÄss") }), env);
  assert.equal(ok.status, 200);
  assert.equal(await ok.text(), "STATIC");
  const bad = await worker.fetch(get("/simple/", { Authorization: basic("üser", "pÅss") }), env);
  assert.equal(bad.status, 401);
});

// --- routing --------------------------------------------------------------

test("an index path is delegated to the static assets", async (t) => {
  installStubs(t);
  const response = await worker.fetch(auth("/simple/foo/"), makeEnv());
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "STATIC");
});

test("the manifest itself is delegated, not redirected", async (t) => {
  installStubs(t);
  const response = await worker.fetch(auth("/_assets/manifest.json"), makeEnv());
  assert.equal(response.status, 200);
  assert.equal(JSON.parse(await response.text()).version, 1);
});

test("an asset path with no filename is delegated", async (t) => {
  const stubs = installStubs(t);
  const response = await worker.fetch(auth("/_assets/11"), makeEnv());
  assert.equal(response.status, 200);
  assert.equal(stubs.upstreamCalls(), 0);
});

// --- the allow-list -------------------------------------------------------

test("an unknown id is not found", async (t) => {
  const stubs = installStubs(t);
  const response = await worker.fetch(auth("/_assets/99/demo-1.0-py3-none-any.whl"), makeEnv());
  assert.equal(response.status, 404);
  assert.equal(stubs.upstreamCalls(), 0);
});

test("a non-numeric id is refused even when the manifest lists it", async (t) => {
  // JSON.parse makes "__proto__" an *own* property, so the own-property lookup
  // finds a usable entry here: only the id shape check can refuse it.
  const stubs = installStubs(t);
  const env = envWithAssets(
    JSON.parse('{"__proto__": {"repo": "o/r", "filename": "demo-1.0-py3-none-any.whl"}}'),
  );
  const response = await worker.fetch(
    auth("/_assets/__proto__/demo-1.0-py3-none-any.whl"),
    env,
  );
  assert.equal(response.status, 404);
  assert.equal(stubs.upstreamCalls(), 0);
});

test("an inherited manifest key cannot forge an entry", async (t) => {
  // Only the own-property lookup stands between a polluted prototype and a
  // fetch of an id the manifest never listed. The id must be the inherited
  // key: a plain `assets[id]` walks the prototype chain, `hasOwnProperty`
  // does not. A far-out number keeps the pollution from colliding with any
  // ordinary array index while it is installed.
  const stubs = installStubs(t);
  const forged = "987654321";
  Object.defineProperty(Object.prototype, forged, {
    value: { repo: "o/r", filename: "demo-1.0-py3-none-any.whl" },
    configurable: true,
    writable: true,
  });
  t.after(() => delete Object.prototype[forged]);
  const response = await worker.fetch(
    auth(`/_assets/${forged}/demo-1.0-py3-none-any.whl`),
    envWithAssets({}),
  );
  assert.equal(response.status, 404);
  assert.equal(stubs.upstreamCalls(), 0);
});

test("a filename mismatch is not found", async (t) => {
  const stubs = installStubs(t);
  const response = await worker.fetch(auth("/_assets/11/evil-9.9-py3-none-any.whl"), makeEnv());
  assert.equal(response.status, 404);
  assert.equal(stubs.upstreamCalls(), 0);
});

test("a metadata filename mismatch is not found", async (t) => {
  const stubs = installStubs(t);
  const response = await worker.fetch(
    auth("/_assets/11/evil-9.9-py3-none-any.whl.metadata"),
    makeEnv(),
  );
  assert.equal(response.status, 404);
  assert.equal(stubs.upstreamCalls(), 0);
});

test("malformed percent-encoding is not found", async (t) => {
  const stubs = installStubs(t);
  const response = await worker.fetch(auth("/_assets/11/%E0%A4%A"), makeEnv());
  assert.equal(response.status, 404);
  assert.equal(stubs.upstreamCalls(), 0);
});

// --- one test per manifest-entry guard, each failing alone ----------------

test("a non-string repo is not found", async (t) => {
  // ["o/r"] stringifies to "o/r" and passes both the pattern and the ".."
  // check: only the typeof guard refuses it.
  const stubs = installStubs(t);
  const env = envWithAssets({
    11: { repo: ["o/r"], filename: "demo-1.0-py3-none-any.whl" },
  });
  const response = await worker.fetch(auth(WHEEL), env);
  assert.equal(response.status, 404);
  assert.equal(stubs.upstreamCalls(), 0);
});

test("a repo with characters outside the pattern is not found", async (t) => {
  // A string, and no "..": only the pattern refuses it.
  const stubs = installStubs(t);
  const env = envWithAssets({
    11: { repo: "o/r?private=1", filename: "demo-1.0-py3-none-any.whl" },
  });
  const response = await worker.fetch(auth(WHEEL), env);
  assert.equal(response.status, 404);
  assert.equal(stubs.upstreamCalls(), 0);
});

test("a repo that walks the api path is not found", async (t) => {
  // "../.." is two pattern-legal segments: only the ".." check refuses it.
  const stubs = installStubs(t);
  const env = envWithAssets({ 11: { repo: "../..", filename: "demo-1.0-py3-none-any.whl" } });
  const response = await worker.fetch(auth(WHEEL), env);
  assert.equal(response.status, 404);
  assert.equal(stubs.upstreamCalls(), 0);
});

test("a non-string filename is not found", async (t) => {
  const stubs = installStubs(t);
  const env = envWithAssets({ 11: { repo: "o/r", filename: null } });
  const response = await worker.fetch(auth(WHEEL), env);
  assert.equal(response.status, 404);
  assert.equal(stubs.upstreamCalls(), 0);
});

test("a non-numeric metadata_id is not found", async (t) => {
  const stubs = installStubs(t);
  const env = envWithAssets({
    11: { repo: "o/r", filename: "demo-1.0-py3-none-any.whl", metadata_id: "12/../99" },
  });
  const response = await worker.fetch(auth(`${WHEEL}.metadata`), env);
  assert.equal(response.status, 404);
  assert.equal(stubs.upstreamCalls(), 0);
});

test("an unreadable manifest is not found rather than fetched", async (t) => {
  const stubs = installStubs(t);
  const env = makeEnv({ ASSETS: { fetch: async () => new Response("nope", { status: 404 }) } });
  const response = await worker.fetch(auth(WHEEL), env);
  assert.equal(response.status, 404);
  assert.equal(stubs.upstreamCalls(), 0);
});

test("an unparseable manifest is not found rather than fetched", async (t) => {
  const stubs = installStubs(t);
  const env = makeEnv({ ASSETS: { fetch: async () => new Response("{", { status: 200 }) } });
  const response = await worker.fetch(auth(WHEEL), env);
  assert.equal(response.status, 404);
  assert.equal(stubs.upstreamCalls(), 0);
});

// --- redirecting ----------------------------------------------------------

test("a listed asset redirects to the signed url", async (t) => {
  const stubs = installStubs(t);
  const response = await worker.fetch(auth(WHEEL), makeEnv());
  assert.equal(response.status, 302);
  assert.equal(response.headers.get("Location"), SIGNED);
  assert.equal(stubs.upstreamCalls(), 1);
  assert.equal(stubs.requests[0].url, "https://api.github.com/repos/o/r/releases/assets/11");
});

test("a percent-encoded filename still matches", async (t) => {
  const stubs = installStubs(t);
  const response = await worker.fetch(auth("/_assets/11/demo-1.0-py3-none-any%2Ewhl"), makeEnv());
  assert.equal(response.status, 302);
  assert.equal(stubs.upstreamCalls(), 1);
});

test("metadata with a sidecar redirects to the sidecar id", async (t) => {
  const stubs = installStubs(t);
  const response = await worker.fetch(auth(`${WHEEL}.metadata`), makeEnv());
  assert.equal(response.status, 302);
  assert.equal(response.headers.get("Location"), SIGNED);
  assert.equal(stubs.requests[0].url, "https://api.github.com/repos/o/r/releases/assets/12");
});

test("metadata without a sidecar is served from the static file", async (t) => {
  const stubs = installStubs(t);
  const response = await worker.fetch(
    auth("/_assets/21/solo-1.0-py3-none-any.whl.metadata"),
    makeEnv(),
  );
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "STATIC");
  assert.equal(stubs.upstreamCalls(), 0);
});

// --- headers on delegated responses ---------------------------------------
//
// `_headers` is documented not to apply to responses generated by a Pages
// Function, and in advanced mode every response is one. So these headers are
// the Worker's job; a `_headers` file asserting them would be inert.

test("an extracted sidecar is typed so a resolver can read it", async (t) => {
  // No known extension, so Pages would otherwise guess text/plain.
  installStubs(t);
  const response = await worker.fetch(
    auth("/_assets/21/solo-1.0-py3-none-any.whl.metadata"),
    makeEnv(),
  );
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Content-Type"), "application/octet-stream");
  assert.match(response.headers.get("Cache-Control") || "", /immutable/);
});

test("an index page is cached briefly and privately", async (t) => {
  installStubs(t);
  const response = await worker.fetch(auth("/simple/foo/"), makeEnv());
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Cache-Control"), "private, max-age=300");
  // an index page is not a sidecar: it must keep whatever type Pages served
  assert.notEqual(response.headers.get("Content-Type"), "application/octet-stream");
});

test("every delegated 200 is labelled private, never public", async (t) => {
  // The whole site is behind Basic auth, so `public` anywhere here would let a
  // proxy serve a private index to a client that never authenticated. Only the
  // 200s are labelled at all — a non-200 passes through untouched, which
  // "a static miss is not relabelled" below covers.
  installStubs(t);
  for (const path of [
    "/",
    "/simple/foo/",
    "/_assets/manifest.json",
    "/_assets/11",
    "/_assets/21/solo-1.0-py3-none-any.whl.metadata",
  ]) {
    const response = await worker.fetch(auth(path), makeEnv());
    const control = response.headers.get("Cache-Control") || "";
    assert.match(control, /private/, `${path}: not private`);
    assert.doesNotMatch(control, /public/, `${path}: shared-cacheable`);
  }
});

test("a static miss is not relabelled", async (t) => {
  // An immutable year on a 404 would pin the miss in every cache between here
  // and the client long after the file appeared.
  installStubs(t);
  const env = makeEnv({
    ASSETS: { fetch: async () => new Response("nope", { status: 404 }) },
  });
  const response = await worker.fetch(auth("/simple/gone/"), env);
  assert.equal(response.status, 404);
  assert.equal(response.headers.get("Cache-Control"), null);
});

// --- upstream failures ----------------------------------------------------

test("an upstream failure never leaks GitHub's answer", async (t) => {
  installStubs(t, {
    upstream: async () =>
      new Response(`bad credentials for ${TOKEN}`, {
        status: 401,
        headers: { "X-Debug": TOKEN },
      }),
  });
  const response = await worker.fetch(auth(WHEEL), makeEnv());
  assert.equal(response.status, 502);
  assert.equal(await response.text(), "Upstream error\n");
  for (const [, value] of response.headers) assert.ok(!value.includes(TOKEN));
});

test("a non-302 carrying a Location is still an upstream error", async (t) => {
  // Only the status check refuses this one.
  installStubs(t, {
    upstream: async () => new Response(null, { status: 200, headers: { Location: SIGNED } }),
  });
  const response = await worker.fetch(auth(WHEEL), makeEnv());
  assert.equal(response.status, 502);
});

test("a 302 with no Location is an upstream error", async (t) => {
  // Only the missing-location check refuses this one.
  installStubs(t, { upstream: async () => new Response(null, { status: 302 }) });
  const response = await worker.fetch(auth(WHEEL), makeEnv());
  assert.equal(response.status, 502);
});

test("an upstream network error is a 502", async (t) => {
  installStubs(t, {
    upstream: async () => {
      throw new Error(`connect failed with ${TOKEN}`);
    },
  });
  const response = await worker.fetch(auth(WHEEL), makeEnv());
  assert.equal(response.status, 502);
  assert.ok(!(await response.text()).includes(TOKEN));
});

// --- no caching of signed urls --------------------------------------------

test("every download makes its own upstream call — signed urls are never reused", async (t) => {
  // A cached signed URL minted with one user's authorization would answer
  // another user's request under per-user tokens, so there is no cache at all.
  const stubs = installStubs(t);
  const first = await worker.fetch(auth(WHEEL), makeEnv());
  assert.equal(first.status, 302);
  const second = await worker.fetch(auth(WHEEL), makeEnv());
  assert.equal(second.status, 302);
  assert.equal(second.headers.get("Location"), SIGNED);
  assert.equal(stubs.upstreamCalls(), 2);
});

test("the served redirect stays private", async (t) => {
  installStubs(t);
  const response = await worker.fetch(auth(WHEEL), makeEnv());
  assert.match(response.headers.get("Cache-Control") || "", /private/);
});

// --- the token ------------------------------------------------------------

test("the manifest fetch carries no client headers", async (t) => {
  installStubs(t);
  const seen = [];
  const env = makeEnv();
  const inner = env.ASSETS.fetch;
  env.ASSETS.fetch = async (request) => {
    seen.push(request);
    return inner(request);
  };
  await worker.fetch(auth(WHEEL), env);
  const manifestRequest = seen.find(
    (request) => new URL(request.url).pathname === "/_assets/manifest.json",
  );
  assert.ok(manifestRequest);
  assert.equal(manifestRequest.headers.get("Authorization"), null);
});

test("the token appears in no response on any path", async (t) => {
  installStubs(t);
  const paths = [
    "/",
    "/simple/",
    "/simple/foo/",
    "/_assets/manifest.json",
    "/_assets/11",
    WHEEL,
    `${WHEEL}.metadata`,
    "/_assets/21/solo-1.0-py3-none-any.whl",
    "/_assets/21/solo-1.0-py3-none-any.whl.metadata",
    "/_assets/99/demo-1.0-py3-none-any.whl",
    "/_assets/11/evil-9.9-py3-none-any.whl",
    "/_assets/11/%E0%A4%A",
  ];
  for (const path of paths) {
    for (const request of [get(path), auth(path)]) {
      const response = await worker.fetch(request, makeEnv());
      for (const [name, value] of response.headers) {
        assert.ok(!value.includes(TOKEN), `${path}: token in header ${name}`);
      }
      assert.ok(!(await response.text()).includes(TOKEN), `${path}: token in body`);
    }
  }
});

// --- github-token mode -----------------------------------------------------
//
// Driven through the env fallback the sentinel constants leave open for the
// pristine source; a deployed worker carries baked values instead, so these
// bindings can never flip a production site's mode.

const GATE_URL = "https://api.github.com/repos/o/idx";

function makeGithubEnv(overrides = {}) {
  const { ASSETS } = makeEnv();
  return {
    GHR_PYPI_AUTH_MODE: "github",
    GHR_PYPI_GATE_REPO: "o/idx",
    ASSETS,
    ...overrides,
  };
}

/** Answer the gate and the asset API separately, so tests can vary each. */
function githubStubs(t, { gate = 200, asset = 302, gateThrows = false } = {}) {
  return installStubs(t, {
    upstream: async (url, init) => {
      if (url === GATE_URL) {
        if (gateThrows) throw new Error("gate unreachable");
        return new Response("{}", { status: gate });
      }
      if (asset === 302) {
        return new Response(null, { status: 302, headers: { Location: SIGNED } });
      }
      return new Response(`refused ${init.headers.Authorization}`, { status: asset });
    },
  });
}

const tokenAuth = (path) => get(path, { Authorization: basic("anything", PAT) });

test("github: a token that reads the gate repo sees the index", async (t) => {
  const stubs = installStubs(t, {
    upstream: async () => new Response("{}", { status: 200 }),
  });
  const response = await worker.fetch(tokenAuth("/simple/foo/"), makeGithubEnv());
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "STATIC");
  assert.equal(stubs.upstreamCalls(), 1);
  assert.equal(stubs.requests[0].url, GATE_URL);
  assert.equal(stubs.requests[0].init.headers.Authorization, `Bearer ${PAT}`);
});

test("github: a token that cannot see the gate repo is unauthorized", async (t) => {
  // GitHub answers 404 for repositories a token cannot see; 401 and 403 are
  // the same fact. All three must re-challenge, not 404.
  for (const status of [401, 403, 404]) {
    const stubs = githubStubs(t, { gate: status });
    const response = await worker.fetch(tokenAuth("/simple/foo/"), makeGithubEnv());
    assert.equal(response.status, 401, `gate ${status}`);
    assert.match(response.headers.get("WWW-Authenticate") || "", /^Basic /);
    assert.equal(stubs.upstreamCalls(), 1);
  }
});

test("github: a failing gate check is an outage, not a challenge", async (t) => {
  // A 401 here would tell the user to rotate a working token.
  const flaky = githubStubs(t, { gate: 500 });
  assert.equal((await worker.fetch(tokenAuth("/simple/"), makeGithubEnv())).status, 502);
  assert.ok(flaky.upstreamCalls() >= 1);
  githubStubs(t, { gateThrows: true });
  assert.equal((await worker.fetch(tokenAuth("/simple/"), makeGithubEnv())).status, 502);
});

test("github: missing or malformed credentials never reach GitHub", async (t) => {
  const stubs = installStubs(t);
  for (const headers of [
    {},
    { Authorization: `Bearer ${PAT}` },
    { Authorization: "Basic !!!" },
    { Authorization: "Basic " + Buffer.from("nocolon").toString("base64") },
    // An empty password would read as an anonymous GitHub request, quietly
    // turning "no credential" into "public access".
    { Authorization: basic("user", "") },
  ]) {
    const response = await worker.fetch(get("/simple/", headers), makeGithubEnv());
    assert.equal(response.status, 401);
    assert.match(response.headers.get("WWW-Authenticate") || "", /^Basic /);
  }
  assert.equal(stubs.upstreamCalls(), 0);
});

test("github: a download forwards the client token and skips the gate", async (t) => {
  const stubs = githubStubs(t);
  const response = await worker.fetch(tokenAuth(WHEEL), makeGithubEnv());
  assert.equal(response.status, 302);
  assert.equal(response.headers.get("Location"), SIGNED);
  // Exactly one upstream call — the asset API — because GitHub itself is the
  // per-repository authorization for downloads.
  assert.equal(stubs.upstreamCalls(), 1);
  assert.equal(stubs.requests[0].url, "https://api.github.com/repos/o/r/releases/assets/11");
  assert.equal(stubs.requests[0].init.headers.Authorization, `Bearer ${PAT}`);
});

test("github: a download GitHub refuses is forbidden, not an outage", async (t) => {
  for (const status of [401, 403, 404]) {
    githubStubs(t, { asset: status });
    const response = await worker.fetch(tokenAuth(WHEEL), makeGithubEnv());
    assert.equal(response.status, 403, `asset ${status}`);
    const body = await response.text();
    assert.equal(body, "Your token cannot read this repository\n");
    assert.ok(!body.includes(PAT));
  }
});

test("github: an asset GitHub cannot answer is still a 502", async (t) => {
  githubStubs(t, { asset: 500 });
  assert.equal((await worker.fetch(tokenAuth(WHEEL), makeGithubEnv())).status, 502);
});

test("github: the allow-list is checked before any GitHub call", async (t) => {
  const stubs = githubStubs(t);
  const response = await worker.fetch(
    tokenAuth("/_assets/99/demo-1.0-py3-none-any.whl"),
    makeGithubEnv(),
  );
  assert.equal(response.status, 404);
  assert.equal(stubs.upstreamCalls(), 0);
});

test("github: a static sidecar and the manifest sit behind the gate", async (t) => {
  const ok = githubStubs(t);
  const sidecar = "/_assets/21/solo-1.0-py3-none-any.whl.metadata";
  const served = await worker.fetch(tokenAuth(sidecar), makeGithubEnv());
  assert.equal(served.status, 200);
  assert.equal(await served.text(), "STATIC");
  assert.equal(ok.requests[0].url, GATE_URL);
  const refused = githubStubs(t, { gate: 404 });
  for (const path of [sidecar, "/_assets/manifest.json"]) {
    assert.equal((await worker.fetch(tokenAuth(path), makeGithubEnv())).status, 401, path);
  }
  assert.ok(refused.upstreamCalls() >= 2);
});

test("github: a gate that is not a repository bricks the site", async (t) => {
  const stubs = installStubs(t);
  for (const gate of [undefined, "", "junk", "o/idx/extra", "o/..", "../idx"]) {
    const env = makeGithubEnv({ GHR_PYPI_GATE_REPO: gate });
    const response = await worker.fetch(tokenAuth("/simple/"), env);
    assert.equal(response.status, 500, `gate ${gate}`);
    assert.equal(await response.text(), "Worker is not configured\n");
  }
  assert.equal(stubs.upstreamCalls(), 0);
});

test("github: shared-credential secrets are ignored", async (t) => {
  // Bound GHR_PYPI_* secrets must not open a second door: the password half
  // is a GitHub token here, nothing else, so the gate still decides.
  const stubs = githubStubs(t, { gate: 404 });
  const env = makeGithubEnv({
    GHR_PYPI_USER: "u",
    GHR_PYPI_PASSWORD: "p",
    GHR_PYPI_TOKEN: TOKEN,
  });
  const response = await worker.fetch(get("/simple/", { Authorization: CREDS }), env);
  assert.equal(response.status, 401);
  // "p" was treated as a candidate GitHub token and sent to the gate.
  assert.equal(stubs.requests[0].init.headers.Authorization, "Bearer p");
});

test("github: downloads are never cached across requests either", async (t) => {
  const stubs = githubStubs(t);
  await worker.fetch(tokenAuth(WHEEL), makeGithubEnv());
  await worker.fetch(tokenAuth(WHEEL), makeGithubEnv());
  assert.equal(stubs.upstreamCalls(), 2);
});
