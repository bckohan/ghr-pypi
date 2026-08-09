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

Only the ``cloudflare`` target ships a redirector today, so the rest of this guide is
Cloudflare Pages. Any target may declare the same capability — see :ref:`targets-redirect`.

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
   target: cloudflare

.. code-block:: sh

   export GITHUB_TOKEN=...          # must be able to read every listed repository
   ghr-pypi index --config index.yml --out site --target-out ./deploy

That run writes:

``site/simple/``
   The index, with every file link pointing at ``../../_assets/<asset-id>/<filename>``.

``site/_assets/manifest.json``
   The redirector's allow-list of published assets. It is read from the deployed site at
   request time, so publishing a new release is a redeploy — never a Worker redeploy.

``site/_worker.js``
   The redirector itself. Pages reads it from the build output root in *advanced mode* and
   never serves it.

``deploy/wrangler.toml``
   The deploy manifest, and an operator artifact: deliberately outside the published site.

``deploy/SETUP.md``
   A deploy checklist, also operator-side — a page describing how the index is gated must not
   be published. It is the commands from this guide with none of the reasoning, and it links
   back here; this page is where the detail is maintained.

Under the default :ref:`config-missing-metadata` ``extract``, the build also downloads each
wheel whose release carries no ``.metadata`` asset — once, to read its core metadata — and
writes the sidecar into ``site/_assets/<asset-id>/``. That is the one cost this mode pays over
link mode, and it is what makes this the only way to serve :pep:`658` metadata for a private
repository *without mirroring the wheels* — link mode cannot host the sidecars at all, and
mirror mode gets them only by copying every wheel into the site. Set ``missing_metadata: warn``
to skip those downloads and take the per-repository coverage warning instead; publishing the
sidecars from your release workflow (:ref:`howto-publish-metadata`) makes the choice moot.

``wrangler.toml`` hardcodes ``name = "ghr-pypi"``
-------------------------------------------------

If your Pages project is called something else, change it — and note that the file is
**regenerated on every build**, so the rename has to be re-applied each time, or the file kept
somewhere ``--target-out`` does not point at. Wrangler also finds ``wrangler.toml`` by walking
up from the working directory, so run the commands below from the directory holding it.

Deploy it
=========

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

Install from it
===============

``pip`` and ``uv`` both speak Basic auth. Put the credentials in ``~/.netrc``
(``_netrc`` on Windows) and use the plain URL:

.. code-block:: text

   machine packages.yourorg.example
     login <GHR_PYPI_USER>
     password <GHR_PYPI_PASSWORD>

.. code-block:: sh

   pip install --index-url https://packages.yourorg.example/simple/ yourpkg

Or inline them in the index URL, remembering that a password in a URL leaks into shell history
and CI logs:

.. code-block:: sh

   pip install --index-url https://user:pass@packages.yourorg.example/simple/ yourpkg

Nothing else is needed on the client. The redirect the Worker returns is an ordinary 302 to a
signed URL, and the installer follows it with no credentials of its own.

Security notes
==============

**Cloudflare Access is not a substitute for the Worker's Basic auth.** Access authenticates
service tokens with the ``CF-Access-Client-Id`` and ``CF-Access-Client-Secret`` headers, and
``pip`` cannot send arbitrary headers per URL — which is the same limitation that makes this
whole mode necessary. Access in front of the site is fine for humans browsing the index in a
browser; it cannot replace the credentials an installer presents.

**The index is gated, not just ``_assets/``.** Advanced mode routes every request through the
Worker, so the landing page, ``simple/``, and the manifest are all behind the same Basic auth.
That is deliberate: an ungated index of a private repository publishes your project names,
version history and release cadence to anyone who finds the URL.

**The manifest is an allow-list, and it is why the redirector is safe to expose.** The Worker
serves only what ``_assets/manifest.json`` lists. Without it, anyone who reached the index
could have the Worker fetch *any* asset the ``GITHUB_TOKEN`` can read — including repositories
this index never listed, since the token's reach is wider than the build's. With it, an id
that is not in the manifest is a 404, and an id fetched under the wrong filename is a 404 too.

**The manifest discloses more than the index does.** For every published asset it records the
source repository as ``owner/name``, and it sits at a predictable URL. The index pages carry
no such thing: under this mode every link is ``../../_assets/<id>/<filename>``, and neither
the HTML nor the :pep:`691` JSON emits the source repository at all. So the manifest maps each
file back to the GitHub repository it came from — exposing your organization name and the
names of private repositories that the index beside it never mentions.

It is behind the same Basic auth as everything else, so this is not a leak; it is a reason to
count the manifest as part of what you are gating rather than as an internal file that happens
to be reachable. If a repository's *name* is itself sensitive, note that this mode publishes
it to everyone holding the index credentials, and mirroring does not.

**Every delegated 200 is marked** ``private``, as are the signed-URL redirects. The site is
authenticated, so no shared cache may store the index pages, the extracted sidecars, or the
redirects. Non-200 responses — 401, 404, ``502 Upstream error``, and the 304s Pages answers a
conditional request with — pass through with no ``Cache-Control`` at all, deliberately: an
immutable year on a 404 would pin a miss in every cache between the Worker and the client long
after the file appeared. The Worker sets all of this itself; a ``_headers`` file would not
apply to a Pages Function's response, which is why the target writes none in this mode.

Next
====

* :ref:`config-assets` — the three modes side by side, and what ``redirect`` writes.
* :ref:`config-missing-metadata` — ``extract`` versus ``warn``, and why it is redirect-only.
* :ref:`targets` — the ``cloudflare`` target's two shapes, and ``supports_redirect``.
* :ref:`howto-private-repository` — the mirroring answer to the same question.
* :ref:`cli` — every failure this mode can raise, verbatim.
