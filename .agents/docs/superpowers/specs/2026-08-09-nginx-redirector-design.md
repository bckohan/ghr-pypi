# The nginx Redirector — Design

**Date:** 2026-08-09
**Status:** Approved

## Problem

`assets: redirect` serves a private index without mirroring: the published
download URL points at the site's own `/_assets/<id>/<filename>`, and a
token-holding hop turns that into GitHub's signed 302. Only the `cloudflare`
target can do it. `NginxTarget` emits a static-serving snippet and declares no
`supports_redirect`, so `ghr-pypi index --assets redirect --target nginx`
refuses.

That leaves the operator who runs their own server with only two options for a
private index: mirror every asset, or don't have one.

## Programme position

This is the redirector half of `direction.md`'s remaining target work. It does
not address the other half (`_redirects` alongside `_headers`), and it is a
prerequisite for the nginx tutorial in the tutorial sub-project that follows.

## The finding that shapes it

**njs is not standard nginx.** It is an official F5 module, but it ships
separately — `nginx-module-njs`, `libnginx-mod-http-js`, `nginx-mod-http-js`
depending on the distribution — and requires a top-level
`load_module modules/ngx_http_js_module.so;`. The official Docker images do not
bundle it. `ngx_http_auth_request_module` is not compiled by default either,
and additionally needs a backend service to answer the subrequest.

So the obvious ports of the Cloudflare Worker both open with "install a module
and edit your main nginx config", which is a poor first step for a target whose
whole shape is "here is a file, `include` it".

**None of it is necessary, because we generate the config.** The Worker reads
`manifest.json` at runtime because it is a static artifact deployed once, ahead
of the assets it will serve. nginx has no such constraint: `ghr-pypi` knows
every asset at build time and can emit the allow-list as configuration. That
turns the whole redirector into stock nginx — `auth_basic`, `map`, `proxy_pass`
— with nothing to install.

## Design

### Two files, because `map` is `http`-context only

`ghr-pypi.conf` is `include`d inside a `server` block. A `map` directive is
only valid in `http` context, so the allow-list cannot live in it. In redirect
mode `NginxTarget` emits a second file, `ghr-pypi-assets.conf`, to be included
at `http` level. Both go to `target_dir`, never `out_dir` — a server
configuration written into the published tree would be served to anyone who can
reach the index.

In link and mirror mode nothing changes and the second file is not written.

### The allow-list is a generated `map`, keyed on the full URI

```nginx
map $uri $ghr_pypi_asset {
    default "";
    "/_assets/12345/pkg-1.0-py3-none-any.whl"           "/repos/yourorg/lib/releases/assets/12345";
    "/_assets/12345/pkg-1.0-py3-none-any.whl.metadata"  "/repos/yourorg/lib/releases/assets/12346";
}
```

`redirect_urls()` already publishes `../../_assets/<id>/<filename>`, so the map
key is exactly the URI that arrives. The value is the **full upstream path**
rather than the bare asset id, which carries the per-asset repository along —
an index may aggregate many — and keeps the token-holding location generic.

Exact-match keying pins filename to id. This is marginally stricter than the
Worker, which validates the id and ignores the rest of the path.

`.metadata` gets its own entry resolving to the sidecar's asset id, mirroring
the Worker's `metadata_id` handling. Assets whose release has no sidecar get no
`.metadata` key and therefore 404, which is the correct answer. Sidecars
produced by `missing_metadata: extract` are ordinary static files in `out_dir`
and are served by the existing `try_files`, not by this map.

The same collision rule as `write_manifest` applies: two entries claiming one
asset id is refused at build time, not resolved last-wins.

### The server-context half

```nginx
location /_assets/ {
    auth_basic "…";
    auth_basic_user_file /etc/nginx/ghr-pypi.htpasswd;
    if ($ghr_pypi_asset = "") { return 404; }
    include /etc/nginx/ghr-pypi-token.conf;
    proxy_pass https://api.github.com$ghr_pypi_asset;
}
```

`if` with `return` is one of the two documented-safe uses of `if` in a
`location`.

nginx returns GitHub's 302 to the client rather than following it, which is the
Worker's behaviour. The `Authorization` header is set on the upstream request
only and never reaches the client.

### The generated config contains no secrets

`ghr-pypi` emits an `include` of a file it does not write. The operator creates
`ghr-pypi-token.conf` containing `proxy_set_header Authorization "Bearer …"`,
mode 0600, and creates the htpasswd file with `htpasswd`.

This is a deliberate deviation from the Cloudflare target, where the token is a
Pages secret bound at deploy time. There the secret never touches generated
output; here the generated output is a file that lands in a build directory and
plausibly a git repository. A build tool that writes credentials into its own
output is a mistake no amount of documentation repairs, and the same reasoning
that makes `write_manifest` an allow-list rather than an open proxy applies:
the failure mode must be structural, not procedural.

The config must therefore fail closed and legibly when those files are absent —
nginx refuses to start on a missing `include`, which is the desired behaviour
and must be stated in the generated header comment.

### Auth model

HTTP Basic, matching the Worker, because pip can carry credentials in the index
URL and cannot send arbitrary per-index headers.

Basic auth guards `/_assets/` in the sketch above. Whether it should also guard
the index pages is an operator decision, not ours: the generated snippet
documents both and leaves the index-page stanza commented, since an index whose
pages are public but whose assets are private is a legitimate configuration and
the reverse is not.

## What changes in code

- `NginxTarget.supports_redirect = True`.
- `NginxTarget.emit` writes the second file in redirect mode only.
- `tests/test_targets.py:117` currently asserts
  `not hasattr(get_target("nginx"), "supports_redirect")` — it inverts.
- The CLI's redirect-capable-target list (`cli.py:271-277`) picks the change up
  through `getattr`, but its error message enumerates capable targets and must
  be re-checked.

## Verify during implementation, do not assume

Both are ordinary nginx and both are the class of detail this project has
already shipped wrong:

- A `proxy_pass` containing a variable requires a `resolver` directive and
  re-resolves per request. The generated config **carries one**, with a
  documented default the operator can override in place. It is not
  operator-supplied: nginx cannot read `/etc/resolv.conf`, so omitting it
  produces a runtime failure on the first asset request rather than a refusal
  at startup, and a redirector that starts cleanly and then 502s on every
  download is the worst available outcome. Confirm the directive's placement —
  it belongs in the `location`, not the `http` file.
- A large `map` may need `map_hash_bucket_size` raised. Determine the real
  threshold rather than setting it defensively.

## Tests

Unit tests on the generated text, as today.

**Plus a live nginx run.** Generate a config, start real nginx against a stub
upstream, and drive it: an allow-listed asset proxies and returns the upstream
302; a path not in the map 404s; a request without credentials 401s; the
`Authorization` header never appears in a client-visible response; a
`.metadata` path resolves to the sidecar's id and not the wheel's.

This project has twice shipped tests asserting on substrings of generated
config that could never fail — `types{` with no space, a `_headers` file
collapsed to one line. Config that is only ever asserted against as text is
config nothing has run.

## Out of scope

- `_redirects` alongside `_headers` — the other open `direction.md` item.
- The four tutorials, including the nginx one. They come next and are written
  against this.
- Any change to the Cloudflare target or the Worker.
- Mirror and link mode behaviour.
