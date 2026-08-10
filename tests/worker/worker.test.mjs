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
// A query string: the Cache API refuses to store a 3xx whose Location carries
// one under a query-less key, which is why the Worker caches a 200 instead.
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
const originalCaches = globalThis.caches;

/**
 * A stub cache that refuses what Cloudflare's Cache API refuses.
 *
 * Storing a `private` response, or a 301/302 whose Location has a query string
 * under a query-less key, is a documented silent no-op. Mirroring that here is
 * what makes "the cache actually caches" an expressible property.
 */
function makeCache(store, options) {
  // Deliberately not `async`: `putThrowsSync` must throw *before* a promise
  // exists, which is the only thing the Worker's `try` can catch.
  const put = (key, value) => {
    if (options.putThrowsSync) throw new Error("cache put threw synchronously");
    return (async () => {
      if (options.putGate) await options.putGate;
      if (options.putThrows) throw new Error("cache put failed: 413");
      const control = value.headers.get("Cache-Control") || "";
      if (/private|no-store|no-cache/.test(control)) return;
      if (value.status === 301 || value.status === 302) {
        const location = value.headers.get("Location") || "";
        if (!new URL(key.url).search && new URL(location, key.url).search) return;
      }
      store.set(key.url, value);
    })();
  };
  return {
    default: {
      match: async (key) => {
        if (options.matchThrows) throw new Error("cache match failed");
        return store.get(key.url)?.clone();
      },
      put,
    },
  };
}

/**
 * `caches` and `fetch` are globals the Worker reads. Each test installs its own
 * pair so no cache entry or call count leaks between tests.
 */
function installStubs(t, options = {}) {
  const store = new Map();
  globalThis.caches = options.noCaches ? undefined : makeCache(store, options);
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
    globalThis.caches = originalCaches;
  });
  return { requests, store, upstreamCalls: () => requests.length };
}

/** A Cloudflare-style execution context whose deferred work can be inspected. */
function makeCtx() {
  const pending = [];
  return {
    waitUntil: (promise) => pending.push(promise),
    handed: () => pending,
    settle: () => Promise.all(pending),
  };
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

// --- caching --------------------------------------------------------------

test("a second request inside the cache window makes no upstream call", async (t) => {
  const stubs = installStubs(t);
  const first = await worker.fetch(auth(WHEEL), makeEnv());
  assert.equal(first.status, 302);
  const second = await worker.fetch(auth(WHEEL), makeEnv());
  assert.equal(second.status, 302);
  assert.equal(second.headers.get("Location"), SIGNED);
  assert.equal(stubs.upstreamCalls(), 1);
});

test("the cached entry is a shape the cache will actually store", async (t) => {
  const stubs = installStubs(t);
  await worker.fetch(auth(WHEEL), makeEnv());
  const stored = [...stubs.store.values()];
  assert.equal(stored.length, 1, "nothing was stored: the cache refused the shape");
  assert.equal(stored[0].status, 200);
  assert.ok(!/private/.test(stored[0].headers.get("Cache-Control") || ""));
});

test("the served redirect stays private", async (t) => {
  installStubs(t);
  const first = await worker.fetch(auth(WHEEL), makeEnv());
  assert.match(first.headers.get("Cache-Control") || "", /private/);
  const second = await worker.fetch(auth(WHEEL), makeEnv());
  assert.match(second.headers.get("Cache-Control") || "", /private/);
});

test("the cache write is handed to waitUntil rather than awaited", async (t) => {
  // The put is held open, so a Worker that awaited it could not answer at all;
  // and the handover itself is asserted, so merely dropping the promise on the
  // floor is not the same as deferring it.
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const stubs = installStubs(t, { putGate: gate });
  const ctx = makeCtx();
  const first = await worker.fetch(auth(WHEEL), makeEnv(), ctx);
  assert.equal(first.status, 302);
  assert.equal(ctx.handed().length, 1, "the write was not handed to waitUntil");
  assert.equal(typeof ctx.handed()[0].then, "function", "waitUntil got a non-promise");
  release();
  await ctx.settle();
  const second = await worker.fetch(auth(WHEEL), makeEnv(), ctx);
  assert.equal(second.headers.get("Location"), SIGNED);
  assert.equal(stubs.upstreamCalls(), 1);
});

test("a throwing waitUntil still serves the redirect", async (t) => {
  const stubs = installStubs(t);
  const ctx = {
    waitUntil: () => {
      throw new Error("waitUntil refused the handover");
    },
  };
  const response = await worker.fetch(auth(WHEEL), makeEnv(), ctx);
  assert.equal(response.status, 302);
  assert.equal(response.headers.get("Location"), SIGNED);
  // The refused handover falls back to an inline write, so the entry is there.
  const second = await worker.fetch(auth(WHEEL), makeEnv(), ctx);
  assert.equal(second.status, 302);
  assert.equal(stubs.upstreamCalls(), 1);
});

test("a synchronously throwing cache put still serves the redirect", async (t) => {
  // `putThrows` rejects a promise; only this one throws before one exists,
  // which is the path the `try` around the call covers.
  const stubs = installStubs(t, { putThrowsSync: true });
  const response = await worker.fetch(auth(WHEEL), makeEnv());
  assert.equal(response.status, 302);
  assert.equal(response.headers.get("Location"), SIGNED);
  const deferred = await worker.fetch(auth(WHEEL), makeEnv(), makeCtx());
  assert.equal(deferred.status, 302);
  assert.equal(stubs.upstreamCalls(), 2, "an unstored entry must simply be refetched");
});

test("a throwing cache put still serves the redirect", async (t) => {
  const stubs = installStubs(t, { putThrows: true });
  const response = await worker.fetch(auth(WHEEL), makeEnv());
  assert.equal(response.status, 302);
  assert.equal(response.headers.get("Location"), SIGNED);
  const second = await worker.fetch(auth(WHEEL), makeEnv());
  assert.equal(second.status, 302);
  assert.equal(stubs.upstreamCalls(), 2, "an unstored entry must simply be refetched");
});

test("a throwing cache match still serves the redirect", async (t) => {
  installStubs(t, { matchThrows: true });
  const response = await worker.fetch(auth(WHEEL), makeEnv());
  assert.equal(response.status, 302);
  assert.equal(response.headers.get("Location"), SIGNED);
});

test("a deferred cache write that throws is not an unhandled rejection", async (t) => {
  installStubs(t, { putThrows: true });
  const ctx = makeCtx();
  const response = await worker.fetch(auth(WHEEL), makeEnv(), ctx);
  assert.equal(response.status, 302);
  await ctx.settle(); // would reject if the Worker handed over a raw promise
});

test("no cache binding at all still serves the redirect", async (t) => {
  const stubs = installStubs(t, { noCaches: true });
  const response = await worker.fetch(auth(WHEEL), makeEnv());
  assert.equal(response.status, 302);
  assert.equal(response.headers.get("Location"), SIGNED);
  assert.equal(stubs.upstreamCalls(), 1);
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
