import { test } from "node:test";
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import worker from "../../src/ghr_pypi/webhook_worker.js";

const SECRET = "s3cret";
const TOKEN = "tok-abc";
const ENV = { WEBHOOK_SECRET: SECRET, GITHUB_TOKEN: TOKEN, INDEX_REPO: "o/idx" };
const DISPATCHES = "https://api.github.com/repos/o/idx/dispatches";

const RELEASE = JSON.stringify({ action: "published", repository: { full_name: "o/lib" } });

function makeEnv(overrides = {}) {
  return { ...ENV, ...overrides };
}

/**
 * The signature GitHub would really send.
 *
 * Computed with node:crypto rather than the Worker's own helper: a verifier
 * that agreed with itself but not with GitHub would otherwise pass every test.
 */
function sign(body) {
  return "sha256=" + createHmac("sha256", SECRET).update(body).digest("hex");
}

/**
 * A delivery. `signature: null` omits the header entirely; any other value is
 * sent verbatim, and the default is the genuine signature for `body`.
 */
function post(body, { event = "release", signature, method = "POST" } = {}) {
  const headers = {};
  if (event !== null) headers["X-GitHub-Event"] = event;
  if (signature !== null) headers["X-Hub-Signature-256"] = signature ?? sign(body);
  return new Request("https://hook.example.com/", { method, headers, body });
}

/** A request that carries no body, for the methods GitHub never uses. */
function bodiless(method) {
  return new Request("https://hook.example.com/", { method });
}

const originalFetch = globalThis.fetch;

/**
 * Stand in for the dispatch API, recording what the Worker sent.
 *
 * The failure response echoes the token back the way a real 401 from GitHub
 * can, so a Worker that forwarded it would be caught by the leak tests rather
 * than passing quietly.
 */
function stubDispatch(t, { ok = true, throws = false } = {}) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    if (throws) throw new Error(`network down while sending ${TOKEN}`);
    if (ok) return new Response(null, { status: 204 });
    return new Response(`bad credentials for ${TOKEN}`, {
      status: 403,
      headers: { "X-Debug": TOKEN },
    });
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  return calls;
}

// --- method ---------------------------------------------------------------

test("a GET is not allowed", async (t) => {
  const calls = stubDispatch(t);
  const response = await worker.fetch(bodiless("GET"), makeEnv());
  assert.equal(response.status, 405);
  assert.equal(response.headers.get("Allow"), "POST");
  assert.equal(calls.length, 0);
});

test("a PUT is not allowed", async (t) => {
  const calls = stubDispatch(t);
  const response = await worker.fetch(post(RELEASE, { method: "PUT" }), makeEnv());
  assert.equal(response.status, 405);
  assert.equal(calls.length, 0);
});

// --- configuration --------------------------------------------------------
//
// Each of these is a correctly signed release: only the missing binding can
// account for the refusal, and an unbound secret must fail closed rather than
// turn every caller into an authenticated one.

test("an unbound webhook secret is a configuration error, not an open door", async (t) => {
  const calls = stubDispatch(t);
  const response = await worker.fetch(post(RELEASE), makeEnv({ WEBHOOK_SECRET: undefined }));
  assert.equal(response.status, 500);
  assert.equal(calls.length, 0);
});

test("an empty webhook secret is refused too", async (t) => {
  // "" is falsy but a perfectly usable HMAC key: without the guard the Worker
  // would happily verify signatures made with the empty secret.
  const calls = stubDispatch(t);
  const response = await worker.fetch(post(RELEASE), makeEnv({ WEBHOOK_SECRET: "" }));
  assert.equal(response.status, 500);
  assert.equal(calls.length, 0);
});

test("an unbound token is a configuration error", async (t) => {
  const calls = stubDispatch(t);
  const response = await worker.fetch(post(RELEASE), makeEnv({ GITHUB_TOKEN: undefined }));
  assert.equal(response.status, 500);
  assert.equal(calls.length, 0);
});

test("an unbound index repo is a configuration error", async (t) => {
  const calls = stubDispatch(t);
  const response = await worker.fetch(post(RELEASE), makeEnv({ INDEX_REPO: undefined }));
  assert.equal(response.status, 500);
  assert.equal(calls.length, 0);
});

test("an index repo outside the name pattern is refused", async (t) => {
  // Bound, non-empty, and no "..": only the pattern refuses it.
  const calls = stubDispatch(t);
  const response = await worker.fetch(post(RELEASE), makeEnv({ INDEX_REPO: "o/idx?x=1" }));
  assert.equal(response.status, 500);
  assert.equal(calls.length, 0);
});

test("an index repo that walks the api path is refused", async (t) => {
  // "../.." is two pattern-legal segments: only the ".." check refuses it.
  const calls = stubDispatch(t);
  const response = await worker.fetch(post(RELEASE), makeEnv({ INDEX_REPO: "../.." }));
  assert.equal(response.status, 500);
  assert.equal(calls.length, 0);
});

test("an index repo with a single-dot segment is refused", async (t) => {
  // "./x" is pattern-legal and contains no "..", so only the dot-segment check
  // can refuse it — and "." is as much a path walk character as "..".
  const calls = stubDispatch(t);
  const response = await worker.fetch(post(RELEASE), makeEnv({ INDEX_REPO: "./x" }));
  assert.equal(response.status, 500);
  assert.equal(calls.length, 0);
});

test("an index repo that is not a string is refused", async (t) => {
  // wrangler [vars] parses JSON, so a mis-set variable can arrive as an array.
  // It coerces its way past the name pattern, and then `.includes("..")` is
  // Array.prototype.includes — it compares whole elements and so answers about
  // something else entirely. Only typing it refuses this.
  const calls = stubDispatch(t);
  const response = await worker.fetch(post(RELEASE), makeEnv({ INDEX_REPO: ["o/idx"] }));
  assert.equal(response.status, 500);
  assert.equal(await response.text(), "Receiver is not configured\n");
  assert.equal(calls.length, 0);
});

// --- the signature --------------------------------------------------------

test("a delivery with no signature is refused", async (t) => {
  const calls = stubDispatch(t);
  const response = await worker.fetch(post(RELEASE, { signature: null }), makeEnv());
  assert.equal(response.status, 401);
  assert.equal(calls.length, 0);
});

test("a malformed signature is refused", async (t) => {
  const calls = stubDispatch(t);
  const response = await worker.fetch(post(RELEASE, { signature: "garbage" }), makeEnv());
  assert.equal(response.status, 401);
  assert.equal(calls.length, 0);
});

test("a same-length wrong signature is refused", async (t) => {
  // One hex character flipped, so the length pre-check cannot answer and the
  // comparison loop itself is what must reject it.
  const calls = stubDispatch(t);
  const genuine = sign(RELEASE);
  const last = genuine.slice(-1);
  const forged = genuine.slice(0, -1) + (last === "0" ? "1" : "0");
  assert.equal(forged.length, genuine.length);
  assert.notEqual(forged, genuine);
  const response = await worker.fetch(post(RELEASE, { signature: forged }), makeEnv());
  assert.equal(response.status, 401);
  assert.equal(calls.length, 0);
});

test("a signature made with the wrong secret is refused", async (t) => {
  const calls = stubDispatch(t);
  const forged = "sha256=" + createHmac("sha256", "not-the-secret").update(RELEASE).digest("hex");
  const response = await worker.fetch(post(RELEASE, { signature: forged }), makeEnv());
  assert.equal(response.status, 401);
  assert.equal(calls.length, 0);
});

test("a tampered body cannot reuse the original signature", async (t) => {
  const calls = stubDispatch(t);
  const tampered = JSON.stringify({ action: "published", repository: { full_name: "evil/pwn" } });
  const response = await worker.fetch(
    post(tampered, { signature: sign(RELEASE) }),
    makeEnv(),
  );
  assert.equal(response.status, 401);
  assert.equal(calls.length, 0);
});

test("the signature is checked against the bytes on the wire, not a re-serialisation", async (t) => {
  // Whitespace and key order that no JSON.stringify would reproduce. A Worker
  // that parsed first and hashed its own re-serialisation would 401 here on a
  // delivery GitHub signed correctly.
  const calls = stubDispatch(t);
  const body = '{ "repository" : {"full_name":  "o/lib"} ,\n "action":"published" }';
  const response = await worker.fetch(post(body), makeEnv());
  assert.equal(response.status, 202);
  assert.equal(calls.length, 1);
  assert.equal(JSON.parse(calls[0].init.body).client_payload.repository, "o/lib");
});

test("a body that is not clean utf-8 still authenticates on its raw bytes", async (t) => {
  // A lone 0x80 cannot survive a decode/encode round trip, so a Worker that
  // hashed `request.text()` would call this delivery forged. It is authentic
  // but unparseable, which is a 400, not a 401.
  const calls = stubDispatch(t);
  const body = Buffer.concat([Buffer.from('{"a":'), Buffer.from([0x80]), Buffer.from("}")]);
  const response = await worker.fetch(post(body), makeEnv());
  assert.equal(response.status, 400);
  assert.equal(calls.length, 0);
});

// --- events ---------------------------------------------------------------

test("a ping is answered so the hook does not look broken", async (t) => {
  const calls = stubDispatch(t);
  const body = JSON.stringify({ zen: "Keep it logically awesome." });
  const response = await worker.fetch(post(body, { event: "ping" }), makeEnv());
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "pong\n");
  assert.equal(calls.length, 0);
});

test("an uninteresting event is acknowledged without a dispatch", async (t) => {
  const calls = stubDispatch(t);
  const response = await worker.fetch(post(RELEASE, { event: "push" }), makeEnv());
  assert.equal(response.status, 204);
  assert.equal(response.body, null);
  assert.equal(calls.length, 0);
});

test("a delivery with no event header dispatches nothing", async (t) => {
  const calls = stubDispatch(t);
  const response = await worker.fetch(post(RELEASE, { event: null }), makeEnv());
  assert.equal(response.status, 204);
  assert.equal(calls.length, 0);
});

// --- dispatching ----------------------------------------------------------

test("a release dispatches a rebuild naming the source repository", async (t) => {
  const calls = stubDispatch(t);
  const response = await worker.fetch(post(RELEASE), makeEnv());
  assert.equal(response.status, 202);
  assert.equal(calls.length, 1, "exactly one dispatch");
  const { url, init } = calls[0];
  assert.equal(url, DISPATCHES);
  assert.equal(init.method, "POST");
  assert.equal(init.headers.Authorization, `Bearer ${TOKEN}`);
  assert.equal(init.headers.Accept, "application/vnd.github+json");
  assert.match(init.headers["User-Agent"], /ghr-pypi/);
  assert.match(init.body, /ghr-pypi-rebuild/);
  assert.match(init.body, /o\/lib/);
  const sent = JSON.parse(init.body);
  assert.equal(sent.event_type, "ghr-pypi-rebuild");
  assert.equal(sent.client_payload.repository, "o/lib");
});

test("the dispatch goes to the configured index repository", async (t) => {
  const calls = stubDispatch(t);
  const response = await worker.fetch(post(RELEASE), makeEnv({ INDEX_REPO: "other/index" }));
  assert.equal(response.status, 202);
  assert.equal(calls[0].url, "https://api.github.com/repos/other/index/dispatches");
});

test("a malformed release payload is a bad request, not a dispatch", async (t) => {
  const calls = stubDispatch(t);
  const response = await worker.fetch(post("{not json"), makeEnv());
  assert.equal(response.status, 400);
  assert.equal(calls.length, 0);
});

test("a payload with no repository still rebuilds", async (t) => {
  // The index is stale either way; the client_payload just cannot name why.
  const calls = stubDispatch(t);
  const response = await worker.fetch(post(JSON.stringify({ action: "published" })), makeEnv());
  assert.equal(response.status, 202);
  assert.equal(JSON.parse(calls[0].init.body).client_payload.repository, "unknown");
});

test("a non-string repository name never reaches the client payload", async (t) => {
  const calls = stubDispatch(t);
  const body = JSON.stringify({ repository: { full_name: { evil: true } } });
  const response = await worker.fetch(post(body), makeEnv());
  assert.equal(response.status, 202);
  assert.equal(JSON.parse(calls[0].init.body).client_payload.repository, "unknown");
});

test("a repository name that is not shaped like one never reaches the client payload", async (t) => {
  // A signed delivery cannot forge this, but the name is read back out by a
  // workflow that interpolates it with ${{ }}, so it is held to owner/repo
  // shape rather than merely to being a string. CRLF is the sharpest witness:
  // it survives every type check and nothing downstream expects a second line.
  const calls = stubDispatch(t);
  const body = JSON.stringify({ repository: { full_name: "o/lib\r\nX: 1" } });
  const response = await worker.fetch(post(body), makeEnv());
  assert.equal(response.status, 202);
  const sent = JSON.parse(calls[0].init.body);
  assert.equal(sent.client_payload.repository, "unknown");
  assert.ok(!calls[0].init.body.includes("X: 1"), "no part of the name rides along");
});

test("a payload that is valid json but not an object still rebuilds", async (t) => {
  const calls = stubDispatch(t);
  const response = await worker.fetch(post("null"), makeEnv());
  assert.equal(response.status, 202);
  assert.equal(JSON.parse(calls[0].init.body).client_payload.repository, "unknown");
});

// --- upstream failures ----------------------------------------------------

test("a refused dispatch never leaks GitHub's answer", async (t) => {
  stubDispatch(t, { ok: false });
  const response = await worker.fetch(post(RELEASE), makeEnv());
  assert.equal(response.status, 502);
  assert.equal(await response.text(), "Dispatch failed\n");
  for (const [, value] of response.headers) assert.ok(!value.includes(TOKEN));
});

test("a network failure is a bad gateway", async (t) => {
  stubDispatch(t, { throws: true });
  const response = await worker.fetch(post(RELEASE), makeEnv());
  assert.equal(response.status, 502);
  assert.ok(!(await response.text()).includes(TOKEN));
});

// --- the token ------------------------------------------------------------

test("the token appears in no response on any path", async (t) => {
  for (const options of [{}, { ok: false }, { throws: true }]) {
    // One stub per behaviour, not one per row: t.after restores are registered
    // for the whole test, so stubbing inside the inner loop would queue 27.
    stubDispatch(t, options);
    for (const [label, request, env] of [
      ["method", bodiless("GET"), makeEnv()],
      ["unconfigured", post(RELEASE), makeEnv({ WEBHOOK_SECRET: undefined })],
      ["bad repo", post(RELEASE), makeEnv({ INDEX_REPO: "../.." })],
      ["unsigned", post(RELEASE, { signature: null }), makeEnv()],
      ["forged", post(RELEASE, { signature: "sha256=00" }), makeEnv()],
      ["ping", post(RELEASE, { event: "ping" }), makeEnv()],
      ["ignored", post(RELEASE, { event: "push" }), makeEnv()],
      ["malformed", post("{not json"), makeEnv()],
      ["release", post(RELEASE), makeEnv()],
    ]) {
      const response = await worker.fetch(request, env);
      for (const [name, value] of response.headers) {
        assert.ok(!value.includes(TOKEN), `${label}: token in header ${name}`);
      }
      const body = response.body === null ? "" : await response.text();
      assert.ok(!body.includes(TOKEN), `${label}: token in body`);
    }
  }
});
