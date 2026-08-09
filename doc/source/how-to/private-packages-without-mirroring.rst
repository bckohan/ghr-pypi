.. include:: ../refs.rst

.. _howto-private-without-mirroring:

=======================================================
How do I serve private packages without mirroring them?
=======================================================

Point the index at your own site with ``assets: redirect`` and let a token-holding redirector
fetch each asset on demand. No package bytes are copied into the site, and no build re-uploads
them: the index links to ``_assets/<asset-id>/<filename>`` on the site itself, and the
redirector turns each of those requests into GitHub's short-lived signed URL.

This is the alternative to :ref:`mirroring <howto-private-repository>`. Both exist for the
same reason — a private repository's release assets need an ``Authorization`` header and an
``Accept: application/octet-stream`` header that ``pip`` will not send, so an index that
merely links to GitHub is useless. Mirroring solves it by moving the bytes at build time;
this solves it by putting something in the serving path that *can* send those headers. GitHub
answers that API call with a 302 to a signed URL which needs no headers at all, and ``pip``
follows it happily.

Two built-in targets ship a redirector, and they answer the same ``_assets/`` paths by
entirely different means. ``cloudflare`` deploys a Pages Worker; ``nginx`` writes a
``proxy_pass`` location that stock nginx serves with **no additional modules** — no njs, no
``auth_request``, nothing to compile in. Pick one with the ``target`` key. The build below is
the same for both; the two deployments follow it in turn, and installing from the result is
identical either way. Any other target may declare the same capability — see
:ref:`targets-redirect`.

Build it
========

``assets: redirect`` needs a configuration file. It has no command line flag, and it needs a
``target``, which cannot be passed alongside ``--config``:

.. code-block:: yaml

   # index.yml
   repositories:
     - yourorg/private-lib
   title: yourorg internal index
   url: https://packages.yourorg.example/
   assets: redirect
   target: cloudflare               # or: nginx

.. code-block:: sh

   export GITHUB_TOKEN=...          # must be able to read every listed repository
   ghr-pypi index --config index.yml --out site --target-out ./deploy

Whichever target you chose, that run writes the site itself the same way:

``site/simple/``
   The index, with every file link pointing at ``../../_assets/<asset-id>/<filename>``.

``site/_assets/manifest.json``
   The published allow-list of assets. The Cloudflare Worker reads it from the deployed site
   at request time, which is why a new release there is a site redeploy and never a Worker
   redeploy. The nginx redirector does not read it — its allow-list is a generated ``map``
   instead — but the file is still written, still published, and still behind the same
   credentials.

What the *target* writes differs, and is listed under each deployment below.

Under the default :ref:`config-missing-metadata` ``extract``, the build also downloads each
wheel whose release carries no ``.metadata`` asset — once, to read its core metadata — and
writes the sidecar into ``site/_assets/<asset-id>/``. That is the one cost this mode pays over
link mode, and it is what makes this the only way to serve :pep:`658` metadata for a private
repository *without mirroring the wheels* — link mode cannot host the sidecars at all, and
mirror mode gets them only by copying every wheel into the site. Set ``missing_metadata: warn``
to skip those downloads and take the per-repository coverage warning instead; publishing the
sidecars from your release workflow (:ref:`howto-publish-metadata`) makes the choice moot.

Deploy it to Cloudflare Pages
=============================

With ``target: cloudflare`` the run above also wrote:

``site/_worker.js``
   The redirector itself. Pages reads it from the build output root in *advanced mode* and
   never serves it.

``deploy/wrangler.toml``
   The deploy manifest, and an operator artifact: deliberately outside the published site.

``deploy/SETUP.md``
   A deploy checklist, also operator-side — a page describing how the index is gated must not
   be published. It is the commands from this guide with none of the reasoning, and it links
   back here; this page is where the detail is maintained.

``wrangler.toml`` hardcodes ``name = "ghr-pypi"``
-------------------------------------------------

If your Pages project is called something else, editing ``name`` works exactly once: the file is
**regenerated on every build**, so the rename is gone after the next one. Pass
``--project-name <yours>`` to every ``wrangler pages deploy`` instead — it overrides the file,
survives regeneration, and is the only form that works from a workflow. Wrangler also finds
``wrangler.toml`` by walking up from the working directory, so run the commands below from the
directory holding it.

The three commands, in order
----------------------------

Three steps, in this order, run from the directory holding ``wrangler.toml``. The order
matters and is explained below it:

.. code-block:: sh

   cd deploy
   wrangler pages project create ghr-pypi        # first time only
   wrangler pages secret put GHR_PYPI_USER
   wrangler pages secret put GHR_PYPI_PASSWORD
   wrangler pages secret put GITHUB_TOKEN
   wrangler pages deploy ../site

Every command is ``wrangler pages ...``. The word is load-bearing: this is a Pages project,
not a Worker, and the plain ``wrangler secret`` family would bind to a Worker instead, leaving
this project with no secrets at all and every request answered ``401``.

.. _howto-redirect-secret-order:

Why the project is created first, and the secrets bound before the deploy
-------------------------------------------------------------------------

Secrets attach to a Pages *project*, so ``wrangler pages secret put`` has nothing to bind to
until one exists — and ``wrangler pages deploy`` does not create it for you. Cloudflare
documents ``wrangler pages project create`` as the way to make one, which is why it leads.

Cloudflare's Pages bindings documentation then states that "when setting secrets with Wrangler
or in the Cloudflare dashboard, it needs to be done before a deployment that uses those
secrets", and — for bindings generally — "redeploy your project for the binding to take
effect". A Pages deployment is immutable in this respect: it binds its environment when it is
created, so a secret added afterwards reaches the *next* deployment, not the live one.

Two practical consequences:

* Binding the secrets first is what makes the first deploy work. The reverse order deploys a
  site whose Worker has no credentials, which then 401s every request — diagnosed below, and
  fixed only by a redeploy, not by binding the secret alone.
* **Rotating or adding a secret later requires another** ``wrangler pages deploy``. Nothing
  warns you; the live deployment simply keeps the values it was created with.

Publishing a new *release* is different, and needs no redeploy of the Worker at all — the
allow-list is read from the deployed ``_assets/manifest.json``, so a plain
``wrangler pages deploy`` of the freshly built site is the whole update.

The three secrets
-----------------

``GHR_PYPI_USER`` and ``GHR_PYPI_PASSWORD``
   The credentials clients present. The Worker **fails closed**: if either one is unbound it
   answers ``401`` to *every* request, index pages included, rather than treating an empty
   value as a credential and publishing a private index. A fresh deploy that 401s everything
   is almost always a missing secret — or a secret bound after that deployment was created —
   not a wrong password.

``GITHUB_TOKEN``
   Must be able to read release assets in every indexed repository. It never leaves the
   Worker — clients only ever see a 302 to a signed URL. When it is missing, expired, or
   cannot read a repository, GitHub answers something other than a redirect and the Worker
   returns ``502 Upstream error`` for package downloads while index pages keep loading
   normally. GitHub's reply is never forwarded, because it can carry token detail, so a 502
   on a download with working index pages means this secret: check
   ``wrangler pages secret list`` first.

Serve it from nginx
===================

With ``target: nginx`` the run above also wrote two files into ``--target-out``, both operator
artifacts and neither of them publishable:

``deploy/ghr-pypi.conf``
   The server-block snippet. Under this mode it carries an ``/_assets/`` location that
   authenticates with ``auth_basic``, serves the sidecars that really are on disk, and hands
   everything else to a named location that adds your token and proxies to GitHub's asset API.
   GitHub's 302 to a signed URL is returned to the client rather than followed, so no package
   bytes pass through the server.

``deploy/ghr-pypi-assets.conf``
   The allow-list, as a generated ``map`` from every published ``_assets/`` URI to the GitHub
   asset API path behind it. **Regenerated on every build.**

Both files are commented at length, and those comments are this target's equivalent of the
Cloudflare ``SETUP.md``: they give the paths to create, the ``htpasswd`` invocation and its
one platform caveat, the ``resolver``, and where the CA bundle lives on distributions other
than Debian — each beside the directive that needs it, and regenerated with the config so they
cannot drift from what you deployed. Read the commands there. What follows is the reasoning
they do not repeat.

Include the two files in two different contexts
-----------------------------------------------

There are two files because ``map`` is valid only in ``http`` context while the rest of the
snippet is ``server`` context, and one file cannot be ``include``\ d at both:

.. code-block:: nginx

   http {
       include /etc/nginx/ghr-pypi-assets.conf;   # http context: the allow-list map

       server {
           listen 443 ssl;
           server_name packages.yourorg.example;
           include /etc/nginx/ghr-pypi.conf;      # server context: root and locations
       }
   }

Copy **both** files to the server on every build, not only the first. The map is compiled when
nginx reads its configuration, so an asset published since the last reload is not in it and a
download of that asset is a 404. A deploy is therefore the site, the two files, and
``nginx -t && nginx -s reload``. This is the one place the nginx redirector is more work than
the Worker, which re-reads its allow-list from the deployed site on every request.

The snippet's ``root`` is the absolute path ``--out`` resolved to on the machine that built
it. If the site is built somewhere else and copied over, either build with an ``--out`` that
matches where it lands or edit that one line after copying.

The two files the build will not write
--------------------------------------

The snippet reaches its GitHub token through an ``include`` of
``/etc/nginx/ghr-pypi-token.conf``, and its passwords through
``auth_basic_user_file /etc/nginx/ghr-pypi.htpasswd``. It creates neither.

That is deliberate, and it is the real difference between this target and the Cloudflare one.
Cloudflare's token is a Pages secret: bound by you, held by the platform, never in a file.
nginx has no such vault, and ``--target-out`` defaults to the working directory — so this
output lands in a build directory and quite plausibly a git repository. A build tool that put
a credential in its own output would put that credential wherever the output goes.

Both files fail closed, but they fail *differently*, and the difference decides where you find
out:

* **The token file missing** is a parse error. ``nginx -t`` fails and nginx will not start.
  You find out on deploy.
* **The htpasswd file missing** is not. ``nginx -t`` passes, nginx starts, the index pages
  serve normally, and every asset request answers ``403`` — ``401`` if no credentials were
  offered — with ``open() ... failed`` in the error log. You find out on the first download.
* **A wrong** ``proxy_ssl_trusted_certificate`` **path** fails like the token file rather than
  like a download. nginx reads the CA bundle at configuration load, so ``nginx -t`` fails with
  ``cannot load certificate``. The generated path is the Debian, Ubuntu and Alpine one; the
  alternatives are listed in the config. The verification itself is not optional — nginx does
  not check an upstream certificate by default, and the request being made carries your token.

None of the three serves an asset to anyone, which is the property that matters.

A token that is expired, or that cannot read one of the indexed repositories, is the failure
that surfaces at the client instead of at deploy time. ``proxy_intercept_errors`` is off, so
what comes back is GitHub's own status rather than a page of this configuration's, and the
index pages keep loading normally throughout — a download failing while the index works points
at the token file.

The ``resolver`` line, and when to change it
--------------------------------------------

The generated location sets ``resolver 1.1.1.1 8.8.8.8 valid=300s;``. It is not a preference:
a ``proxy_pass`` whose URL contains a variable is re-resolved per request, and nginx does not
read ``/etc/resolv.conf``. Leave it out and the server still starts cleanly while every
download fails at runtime.

Replace it if you have a resolver of your own — ``127.0.0.53`` on a systemd-resolved host, or
whatever your network provides. Two public resolvers are a default that works everywhere, not
a recommendation: they see a DNS query for GitHub's API host from your server, and nothing
else.

What ends up behind the password, and what does not
----------------------------------------------------

Only ``/_assets/``. The index pages are served openly, which is the opposite of the Cloudflare
deployment — advanced mode routes *every* request through the Worker, so its Basic auth gates
the whole site. Both are defensible: the filenames a private index lists are disclosed by any
index, and the assets themselves stay gated either way. The generated config carries a
commented-out ``location /simple/`` stanza that closes the gap, and
``site/_assets/manifest.json`` — which does map every file back to its source repository — is
already covered by the ``/_assets/`` rule. Uncomment the stanza if your *project names* are
themselves sensitive.

The ``^~`` on the ``/_assets/`` location is load-bearing, and it is the part most likely to be
tidied away by someone reformatting the config later. A plain prefix location loses to *any*
matching regex location in the same ``server`` block, so one ordinary cache-header rule
elsewhere in your configuration — ``location ~ \.whl$ { }`` is the usual shape — would take
every asset request into a block with no ``auth_basic`` in it and serve private files
unauthenticated.
``^~`` tells nginx to stop considering regex locations once this prefix is the longest match.
It is demonstrated rather than asserted: every test in ``tests/test_nginx_live.py`` runs
against a server block containing exactly that hostile neighbour.

Install from it
===============

``pip`` and ``uv`` both speak Basic auth. Put the credentials in ``~/.netrc``
(``_netrc`` on Windows) and use the plain URL — on Cloudflare the credentials are the two
secrets bound above, on nginx a user in the ``htpasswd`` file:

.. code-block:: text

   machine packages.yourorg.example
     login <user>
     password <password>

.. code-block:: sh

   pip install --index-url https://packages.yourorg.example/simple/ yourpkg

Or inline them in the index URL, remembering that a password in a URL leaks into shell history
and CI logs:

.. code-block:: sh

   pip install --index-url https://user:pass@packages.yourorg.example/simple/ yourpkg

Nothing else is needed on the client. The redirect either redirector returns is an ordinary
302 to a signed URL, and the installer follows it with no credentials of its own.

Security notes
==============

**Cloudflare Access is not a substitute for the Worker's Basic auth.** Access authenticates
service tokens with the ``CF-Access-Client-Id`` and ``CF-Access-Client-Secret`` headers, and
``pip`` cannot send arbitrary headers per URL — which is the same limitation that makes this
whole mode necessary. Access in front of the site is fine for humans browsing the index in a
browser; it cannot replace the credentials an installer presents.

**On Cloudflare the index is gated, not just ``_assets/``; on nginx it is not.** Advanced mode
routes every request through the Worker, so the landing page, ``simple/`` and the manifest are
all behind the same Basic auth. The nginx snippet gates ``/_assets/`` alone and ships a
commented-out stanza for the rest. An ungated index of a private repository publishes your
project names, version history and release cadence to anyone who finds the URL — which is
worth a decision either way, not a default you inherit.

**The allow-list is why either redirector is safe to expose.** The Worker serves only what
``_assets/manifest.json`` lists; nginx serves only what its generated ``map`` lists. Without
one, anyone who reached the index could have the redirector fetch *any* asset the token can
read — including repositories this index never listed, since the token's reach is wider than
the build's. With it, an id that is not listed is a 404, and an id fetched under the wrong
filename is a 404 too. On nginx that map is compiled at configuration load, so the allow-list
in force is the one from the last reload rather than the last build.

**The manifest discloses more than the index does.** For every published asset it records the
source repository as ``owner/name``, and it sits at a predictable URL. The index pages carry
no such thing: under this mode every link is ``../../_assets/<id>/<filename>``, and neither
the HTML nor the :pep:`691` JSON emits the source repository at all. So the manifest maps each
file back to the GitHub repository it came from — exposing your organization name and the
names of private repositories that the index beside it never mentions.

It sits under ``_assets/``, so both deployments gate it, and this is not a leak; it is a reason
to count the manifest as part of what you are gating rather than as an internal file that
happens to be reachable. If a repository's *name* is itself sensitive, note that this mode
publishes it to everyone holding the index credentials, and mirroring does not.

**On Cloudflare, every delegated 200 is marked** ``private``, as are the signed-URL redirects.
The site is authenticated, so no shared cache may store the index pages, the extracted
sidecars, or the redirects. Non-200 responses — 401, 404, ``502 Upstream error``, and the 304s
Pages answers a conditional request with — pass through with no ``Cache-Control`` at all,
deliberately: an immutable year on a 404 would pin a miss in every cache between the Worker
and the client long after the file appeared. The Worker sets all of this itself; a ``_headers``
file would not apply to a Pages Function's response, which is why the target writes none in
this mode.

The nginx snippet sets **no** caching directives at all, in this mode as in the others, so
whatever your server already does applies. If you add ``expires`` or ``Cache-Control`` rules,
keep ``/_assets/`` out of any shared cache: the responses there are a signed redirect and an
authenticated sidecar.

Next
====

* :ref:`config-assets` — the three modes side by side, and what ``redirect`` writes.
* :ref:`config-missing-metadata` — ``extract`` versus ``warn``, and why it is redirect-only.
* :ref:`targets` — what each target emits in each mode, and ``supports_redirect``.
* :ref:`howto-private-repository` — the mirroring answer to the same question.
* :ref:`cli` — every failure this mode can raise, verbatim.
* :ref:`tutorial-cloudflare` and :ref:`tutorial-nginx` — the two deployments above as guided
  walkthroughs, each ending in a ``pip install`` from the finished index.
