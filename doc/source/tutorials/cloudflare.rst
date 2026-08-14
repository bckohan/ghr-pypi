.. include:: ../refs.rst

.. _tutorial-cloudflare:

=====================================
Publish a private index on Cloudflare
=====================================

.. note::

      This tutorial is driven from a template repository:
      `cloudflare-pypi-template <https://github.com/bckohan/cloudflare-pypi-template>`_

:pypi:`pip` does not support the authentication scheme required to download release assets
from private GitHub repositories. This tutorial illustrates how to use
Cloudflare Workers to expose a password-protected index that serves wheels from private GitHub
repositories.

The wheels stay on GitHub — nothing is copied anywhere — and a small Worker at Cloudflare's edge
holds the GitHub token, checks a username and password on every request, and turns each download
into a short-lived signed URL.

This is the guided version of :ref:`howto-private-without-mirroring`, which is where the
reasoning and the failure symptoms are maintained. The steps below build the thing; that page
explains it.

Architecture
============

The following two diagrams illustrate the two primary index work flows: package installation and
release indexing.

Package Installation
--------------------

.. raw:: html

   <figure role="group" style="margin: 1.5em 0;">
   <svg viewBox="0 0 760 410" role="img" fill="currentColor" font-size="12"
        font-family="system-ui, sans-serif" style="max-width: 100%; height: auto;"
        aria-label="pip authenticates to the Worker twice; only the Worker talks to
        GitHub with the token; the wheel bytes flow straight from GitHub to pip.">
     <defs>
       <marker id="cf1a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7"
               markerHeight="7" orient="auto-start-reverse">
         <path d="M0 0L10 5L0 10z" fill="currentColor"/>
       </marker>
       <marker id="cf1b" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7"
               markerHeight="7" orient="auto-start-reverse">
         <path d="M0 0L10 5L0 10z" fill="#d97706"/>
       </marker>
     </defs>
     <g stroke="currentColor" stroke-opacity="0.3" stroke-dasharray="4 4">
       <line x1="110" y1="46" x2="110" y2="398"/>
       <line x1="380" y1="46" x2="380" y2="398"/>
       <line x1="650" y1="46" x2="650" y2="398"/>
     </g>
     <g>
       <rect x="25" y="6" width="170" height="40" rx="6" stroke="currentColor"
             fill="currentColor" fill-opacity="0.06"/>
       <text x="110" y="23" text-anchor="middle" font-weight="600"
             font-size="13">your machine</text>
       <text x="110" y="38" text-anchor="middle" font-size="11" opacity="0.7">pip / uv</text>
       <rect x="295" y="6" width="170" height="40" rx="6" stroke="currentColor"
             fill="currentColor" fill-opacity="0.06"/>
       <text x="380" y="23" text-anchor="middle" font-weight="600"
             font-size="13">Cloudflare</text>
       <text x="380" y="38" text-anchor="middle" font-size="11"
             opacity="0.7">_worker.js at the edge</text>
       <rect x="565" y="6" width="170" height="40" rx="6" stroke="currentColor"
             fill="currentColor" fill-opacity="0.06"/>
       <text x="650" y="23" text-anchor="middle" font-weight="600"
             font-size="13">GitHub</text>
       <text x="650" y="38" text-anchor="middle" font-size="11"
             opacity="0.7">API + release assets</text>
     </g>
     <g stroke="currentColor">
       <line x1="114" y1="84" x2="372" y2="84" marker-end="url(#cf1a)"/>
       <line x1="376" y1="116" x2="118" y2="116" stroke-dasharray="5 3"
             marker-end="url(#cf1a)"/>
       <line x1="114" y1="152" x2="372" y2="152" marker-end="url(#cf1a)"/>
       <line x1="384" y1="204" x2="642" y2="204" stroke="#d97706"
             marker-end="url(#cf1b)"/>
       <line x1="646" y1="248" x2="388" y2="248" stroke-dasharray="5 3"
             marker-end="url(#cf1a)"/>
       <line x1="376" y1="280" x2="118" y2="280" stroke-dasharray="5 3"
             marker-end="url(#cf1a)"/>
       <line x1="114" y1="316" x2="642" y2="316" marker-end="url(#cf1a)"/>
       <line x1="646" y1="348" x2="118" y2="348" stroke-dasharray="5 3"
             marker-end="url(#cf1a)"/>
     </g>
     <text x="245" y="77" text-anchor="middle">① GET /simple/project/ + credentials</text>
     <text x="245" y="109" text-anchor="middle">index page — links end in #sha256=…</text>
     <text x="245" y="145" text-anchor="middle">② GET /_assets/id/wheel + credentials</text>
     <text x="388" y="176" font-size="11" opacity="0.75">credentials ✓ · allow-list ✓</text>
     <text x="515" y="197" text-anchor="middle"
           fill="#d97706">③ asset request — GHR_PYPI_TOKEN</text>
     <text x="515" y="219" text-anchor="middle" font-size="10.5"
           fill="#d97706">the token never leaves the Worker</text>
     <text x="515" y="241" text-anchor="middle">302 — signed URL, expires in seconds</text>
     <text x="245" y="273" text-anchor="middle">302 — signed URL</text>
     <text x="380" y="309" text-anchor="middle">④ follow the signed URL — no credentials</text>
     <text x="380" y="341" text-anchor="middle">wheel bytes — straight from GitHub</text>
     <text x="118" y="372" font-size="11" opacity="0.75">sha256 verified ✓</text>
   </svg>
   <figcaption style="font-size: 0.85em; opacity: 0.75; margin-top: 0.4em;">
   Installing: pip presents the netrc credentials twice — once for the index page, once
   for the download — and the Worker, holding the only copy of the GitHub token, answers
   each allow-listed download with a signed URL. The wheel never crosses Cloudflare.
   </figcaption>
   </figure>

Release Indexing
----------------

.. raw:: html

   <figure role="group" style="margin: 1.5em 0;">
   <svg viewBox="0 0 760 406" role="img" fill="currentColor" font-size="12"
        font-family="system-ui, sans-serif" style="max-width: 100%; height: auto;"
        aria-label="A release triggers repository_dispatch, or the daily cron fires;
        deploy.yml rebuilds the index from every repository's releases and deploys
        the new site to Cloudflare Pages.">
     <defs>
       <marker id="cf2a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7"
               markerHeight="7" orient="auto-start-reverse">
         <path d="M0 0L10 5L0 10z" fill="currentColor"/>
       </marker>
       <marker id="cf2b" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7"
               markerHeight="7" orient="auto-start-reverse">
         <path d="M0 0L10 5L0 10z" fill="#d97706"/>
       </marker>
     </defs>
     <g stroke="currentColor" stroke-opacity="0.3" stroke-dasharray="4 4">
       <line x1="110" y1="46" x2="110" y2="394"/>
       <line x1="380" y1="46" x2="380" y2="394"/>
       <line x1="650" y1="46" x2="650" y2="394"/>
     </g>
     <g>
       <rect x="25" y="6" width="170" height="40" rx="6" stroke="currentColor"
             fill="currentColor" fill-opacity="0.06"/>
       <text x="110" y="23" text-anchor="middle" font-weight="600"
             font-size="13">a releasing repository</text>
       <text x="110" y="38" text-anchor="middle" font-size="11"
             opacity="0.7">any repo in ghr-pypi.yml</text>
       <rect x="295" y="6" width="170" height="40" rx="6" stroke="currentColor"
             fill="currentColor" fill-opacity="0.06"/>
       <text x="380" y="23" text-anchor="middle" font-weight="600"
             font-size="13">index repository</text>
       <text x="380" y="38" text-anchor="middle" font-size="11"
             opacity="0.7">deploy.yml — GitHub Actions</text>
       <rect x="565" y="6" width="170" height="40" rx="6" stroke="currentColor"
             fill="currentColor" fill-opacity="0.06"/>
       <text x="650" y="23" text-anchor="middle" font-weight="600"
             font-size="13">Cloudflare Pages</text>
       <text x="650" y="38" text-anchor="middle" font-size="11"
             opacity="0.7">the served site</text>
     </g>
     <rect x="10" y="64" width="200" height="26" rx="5" stroke="currentColor"
           fill="currentColor" fill-opacity="0.06"/>
     <text x="110" y="81" text-anchor="middle"
           font-size="11">release published, wheel attached</text>
     <g stroke="currentColor">
       <line x1="114" y1="124" x2="372" y2="124" stroke="#d97706"
             marker-end="url(#cf2b)"/>
       <line x1="376" y1="212" x2="118" y2="212" marker-end="url(#cf2a)"/>
       <line x1="114" y1="244" x2="372" y2="244" stroke-dasharray="5 3"
             marker-end="url(#cf2a)"/>
       <line x1="384" y1="330" x2="642" y2="330" marker-end="url(#cf2a)"/>
     </g>
     <text x="245" y="117" text-anchor="middle"
           fill="#d97706">① repository_dispatch — ghr-pypi-rebuild</text>
     <text x="245" y="137" text-anchor="middle" font-size="10.5"
           fill="#d97706">the instant path — one step in its release workflow</text>
     <rect x="270" y="152" width="220" height="26" rx="5" stroke="currentColor"
           fill="currentColor" fill-opacity="0.06"/>
     <text x="380" y="169" text-anchor="middle"
           font-size="11">…or the daily cron — at most a day behind</text>
     <text x="245" y="205" text-anchor="middle">② reads releases — GHR_PYPI_TOKEN</text>
     <text x="245" y="237" text-anchor="middle">assets + digests</text>
     <rect x="270" y="272" width="220" height="26" rx="5" stroke="currentColor"
           fill="currentColor" fill-opacity="0.06"/>
     <text x="380" y="289" text-anchor="middle"
           font-size="11">build: index · allow-list · _worker.js</text>
     <text x="515" y="323" text-anchor="middle">③ wrangler pages deploy</text>
     <text x="515" y="343" text-anchor="middle" font-size="10.5"
           opacity="0.8">creates the project if needed, binds secrets first</text>
     <rect x="555" y="358" width="190" height="26" rx="5" stroke="currentColor"
           fill="currentColor" fill-opacity="0.06"/>
     <text x="650" y="375" text-anchor="middle" font-size="11">the new index is live</text>
   </svg>
   <figcaption style="font-size: 0.85em; opacity: 0.75; margin-top: 0.4em;">
   Releasing: a repository's dispatch — or the daily cron — starts deploy.yml, which reads
   every indexed repository's releases and deploys the rebuilt site, allow-list and Worker
   included, to Cloudflare Pages. A spurious rebuild produces the same site again.
   </figcaption>
   </figure>

Step 1 — Create the index repository *(browser)*
================================================

Open `cloudflare-pypi-template <https://github.com/bckohan/cloudflare-pypi-template>`_ and
click **Use this template → Create a new repository**. Name it anything; private is fine.

This repository publishes no packages of its own and serves nothing. It holds exactly two
things: ``ghr-pypi.yml``, which says what to index and where it will be served, and
``.github/workflows/deploy.yml``, which does everything else.

Step 2 — Create two tokens *(browser)*
======================================

**A GitHub token**, so the build can read releases and the Worker can fetch assets: a
`fine-grained personal access token
<https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens>`_
with **Contents: Read-only** on every repository you will index, and nothing else.

**A Cloudflare API token**, so the workflow can deploy: create one at **My Profile → API
Tokens** with the single permission **Account → Cloudflare Pages → Edit** (the "Cloudflare
Pages" token template does this), and note your `account ID
<https://developers.cloudflare.com/fundamentals/account/find-account-and-zone-ids/>`_ while
you are there.

Step 3 — Configure *(browser)*
==============================

In your new repository, under **Settings → Secrets and variables → Actions**, add five
secrets and one variable:

.. list-table::
   :header-rows: 1
   :widths: 32 14 54

   * - Name
     - Kind
     - Value
   * - ``GHR_PYPI_TOKEN``
     - secret
     - The GitHub token from Step 2. Used twice: by the build to read your releases, and by
       the Worker — where it never leaves — to fetch assets at request time.
   * - ``GHR_PYPI_USER``
     - secret
     - The username clients will install with. Choose it now; it goes in ``~/.netrc`` in
       Step 5.
   * - ``GHR_PYPI_PASSWORD``
     - secret
     - The password clients will install with. Likewise your choice.
   * - ``CLOUDFLARE_API_TOKEN``
     - secret
     - The Cloudflare API token from Step 2.
   * - ``CLOUDFLARE_ACCOUNT_ID``
     - secret
     - Your Cloudflare account ID.
   * - ``CLOUDFLARE_PROJECT_NAME``
     - **variable**
     - The Pages project the workflow will create and deploy to. It names the site:
       ``https://<value>.pages.dev``. ``pages.dev`` hostnames are shared by everyone using
       Cloudflare Pages, so pick something unlikely to be taken.

Then edit ``ghr-pypi.yml`` in the repository root — the three keys marked ``CHANGE ME``:

.. code-block:: yaml

   repositories:
     - yourorg/private-lib
     - yourorg/private-app
   title: yourorg internal index
   url: https://yourorg-pypi.pages.dev/

``repositories`` is what goes in the index; a pattern like ``yourorg/*`` indexes every
repository the token can read in that owner. ``url`` is the address from
``CLOUDFLARE_PROJECT_NAME`` — nothing fetches it; it is what the landing page prints in the
``pip install`` command it shows visitors, so if the two disagree the index still works and
tells people the wrong thing. Committing this edit already triggers the workflow — which will
fail on the next step's business if you commit before configuring the secrets above. That is
harmless; every run is safe to repeat.

Step 4 — Run the workflow *(GitHub Actions)*
============================================

**Actions → Deploy package index → Run workflow.** One run does four things, in an order that
matters:

1. **Builds the index** — ``uvx ghr-pypi index --config ghr-pypi.yml --out site``. The
   ``assets: redirect`` and ``target: cloudflare`` keys in the config make every file link
   point at the site's own ``_assets/`` paths, write the allow-list of published assets
   beside them, and generate the ``_worker.js`` that will answer those paths.
2. **Creates the Pages project** named by ``CLOUDFLARE_PROJECT_NAME``, if it does not exist
   yet.
3. **Binds the three secrets the Worker reads** — ``GHR_PYPI_USER``, ``GHR_PYPI_PASSWORD``,
   ``GHR_PYPI_TOKEN`` — to that project, *before* deploying. A Pages deployment binds its
   environment when it is created, so a secret bound after the fact reaches the next
   deployment, not the live one; binding first, every run, is what makes the order always
   right. It also makes rotation trivial: update the GitHub secret, re-run the workflow.
4. **Deploys** ``site/`` to Cloudflare Pages. The run's summary prints the URL.

Cloudflare reads the ``_worker.js`` at the root of what was deployed and routes **every**
request through it first. That is why authentication gates the whole index and not only the
downloads.

Check the gate from any machine (or just open the URL in a browser — it will ask for
credentials):

.. code-block:: sh

   curl -s -o /dev/null -w '%{http_code}\n' https://<project>.pages.dev/simple/

.. code-block:: text

   401

**A 401 on the index page is the design working**, not a failure — nothing on this site,
landing page included, answers without credentials. If something goes wrong instead, the
symptom names the step: the *create* step failing with "project name already taken" means
pick a new ``CLOUDFLARE_PROJECT_NAME`` (and ``url``) and re-run; the build failing with
``error: provide --token`` means ``GHR_PYPI_TOKEN`` is unset; a 200 here means the deploy
somehow did not include ``_worker.js`` — re-run the workflow and read its build step.
:ref:`howto-private-without-mirroring` has the full symptom table.

Step 5 — Install from it *(your machine)*
=========================================

``pip`` and ``uv`` both speak Basic auth, and both look credentials up in ``~/.netrc``
(``_netrc`` on Windows). Add an entry for the index host, with the two values you chose in
Step 3:

.. code-block:: text

   machine <project>.pages.dev
     login <GHR_PYPI_USER>
     password <GHR_PYPI_PASSWORD>

Then install, with the plain URL and no credentials in it:

.. code-block:: sh

   pip install --index-url https://<project>.pages.dev/simple/ <a project name>

``pip`` reports ``Successfully installed``. Four things happened in that one command: ``pip``
authenticated to the Worker to read the index page, followed a file link to ``_assets/`` on
the same host, the Worker checked the credentials again and looked the asset up in its
allow-list, and ``pip`` followed the returned signed URL — no credentials of its own — and
verified the download against the ``#sha256=`` fragment.

``--no-deps`` is there because ``--index-url`` replaces PyPI entirely, so a package with
dependencies has nowhere to resolve them from; :ref:`howto-avoid-pypi` covers using your
index and PyPI together. Clean up with ``rm -rf /tmp/ghr-pypi-check``.

.. note::

   **One shared password not fine-grained enough?** Adding ``auth: github`` to
   ``ghr-pypi.yml`` switches the index to per-user credentials: each person installs with
   their *own* fine-grained GitHub token as the netrc password, GitHub decides per
   repository what they may download, and the Worker stops holding any secret at all —
   the ``GHR_PYPI_USER`` and ``GHR_PYPI_PASSWORD`` secrets simply stop being needed.
   :ref:`howto-github-auth` has the mode end to end.


Step 6 — Make rebuilds instant *(GitHub Actions, in each releasing repository)*
===============================================================================

Your index describes repositories other than the one it lives in, and GitHub delivers a
``release`` event only to the repository the release happened in. The template already covers
this with a blunt instrument: the workflow reruns **daily**, so the index is never more than a
day behind. Every hop below is optional sharpening.

The workflow also listens for ``repository_dispatch``, and that is the instant path: a
releasing repository tells the index to rebuild *now*. Add one step to the release workflow of
each repository in your index:

.. code-block:: yaml

   - name: Ask the package index to rebuild
     env:
       GH_TOKEN: ${{ secrets.INDEX_DISPATCH_TOKEN }}
     run: |
       gh api repos/YOURORG/YOUR-INDEX-REPO/dispatches \
         -f event_type=ghr-pypi-rebuild \
         -f "client_payload[repository]=$GITHUB_REPOSITORY"

``YOURORG/YOUR-INDEX-REPO`` is the repository you created in Step 1 — the one that *serves*
the index — and ``INDEX_DISPATCH_TOKEN`` is a token with **Contents: Read and write** on it
(that single permission is what ``POST .../dispatches`` requires; the workflow's built-in
token cannot cross repositories). Store it once as an **organization-level** Actions secret
and every repository in the organization can use this step without further setup.

The honest cost: a repository added next year is a repository that silently is not doing
this — its releases surface at the next daily rebuild instead. If you **own the organization**
and want instant coverage of every repository including future ones, one organization webhook
plus a small receiver Worker closes that gap; :ref:`howto-rebuild-on-release` is the full
treatment of all three routes and ``ghr-pypi webhook`` generates the receiver. A spurious
rebuild costs nothing — every run is idempotent — which is why stacking routes is safe and
usual.
