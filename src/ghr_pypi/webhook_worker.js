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
// GitHub owner/repo names use only these characters. Same rule the redirector
// applies to manifest repos.
const REPO = /^[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+$/;
// A whole segment of nothing but dots is what walks a URL path, and no real
// owner or repo is named "." or ".."; refusing those (and any embedded "..")
// is what makes it true that a mis-set INDEX_REPO cannot leave the dispatches
// endpoint it is interpolated into.
const DOT_SEGMENT = /(^|\/)\.+($|\/)/;

function text(status, body, headers = {}) {
  return new Response(`${body}\n`, {
    status,
    headers: { "Content-Type": "text/plain; charset=utf-8", ...headers },
  });
}

// Kept deliberately identical to the redirector's constantTimeEqual in
// targets/_worker.js. Cloudflare's single-file deployment forces the
// duplication; it does not force two implementations, and one comparison
// primitive that behaves the same everywhere is one fewer thing to re-audit.
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

/**
 * The `sha256=<hex>` GitHub would have sent for these bytes.
 *
 * Takes bytes, not a string: GitHub signs the octets on the wire, so these are
 * the octets GitHub signed, by construction. Decoding first — `request.text()`,
 * or a JSON round-trip — would instead make the verdict depend on an assumption
 * about someone else's serializer, and would map every body that is not clean
 * UTF-8 onto the replacement character, so distinct byte strings would share a
 * MAC. Valid-UTF-8 JSON does survive that round trip, emoji and astral-plane
 * characters included, so this is about what the check rests on, not about any
 * genuine delivery it would otherwise turn away.
 */
async function signatureFor(secret, bytes) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, bytes);
  const hex = [...new Uint8Array(mac)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
  return `sha256=${hex}`;
}

export default {
  async fetch(request, env) {
    // 405 carries Allow per RFC 9110 §15.5.6; GitHub only ever POSTs here.
    if (request.method !== "POST") return text(405, "Method not allowed", { Allow: "POST" });
    if (
      !env.WEBHOOK_SECRET ||
      !env.GITHUB_TOKEN ||
      // wrangler [vars] accepts JSON, so INDEX_REPO can arrive as an object;
      // typed first so a non-string fails closed here rather than throwing
      // out of the string methods below.
      typeof env.INDEX_REPO !== "string" ||
      !REPO.test(env.INDEX_REPO) ||
      env.INDEX_REPO.includes("..") ||
      DOT_SEGMENT.test(env.INDEX_REPO)
    ) {
      // Fail closed: an unbound secret must never mean "accept everything",
      // and a repo that is not a repo must never become some other API path.
      return text(500, "Receiver is not configured");
    }

    // The signature covers the raw bytes, so read them before parsing.
    const raw = new Uint8Array(await request.arrayBuffer());
    const provided = request.headers.get("X-Hub-Signature-256") || "";
    const expected = await signatureFor(env.WEBHOOK_SECRET, raw);
    if (!constantTimeEqual(provided, expected)) return text(401, "Bad signature");

    const event = request.headers.get("X-GitHub-Event") || "";
    // GitHub pings on hook creation; rejecting it makes the hook look broken
    // in the UI long before anyone publishes a release.
    if (event === "ping") return text(200, "pong");
    if (event !== "release") return new Response(null, { status: 204 });

    let payload;
    try {
      payload = JSON.parse(new TextDecoder().decode(raw));
    } catch {
      return text(400, "Malformed payload");
    }
    // Only an owner/repo names a repository. Typing it is not enough: this
    // string is read back out by a rebuild workflow that interpolates it with
    // ${{ }}, which is a code-execution sink, so it is held to the same shape
    // as INDEX_REPO — no newlines, quotes, or expression syntax can ride in.
    const claimed = payload && payload.repository ? payload.repository.full_name : undefined;
    const source = typeof claimed === "string" && REPO.test(claimed) ? claimed : "unknown";

    let response;
    try {
      response = await fetch(`https://api.github.com/repos/${env.INDEX_REPO}/dispatches`, {
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
      });
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
