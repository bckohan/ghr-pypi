.. include:: ../refs.rst

.. _tutorial-cloudflare:

=====================================
Publish a private index on Cloudflare
=====================================

In this tutorial you will turn the releases your repositories already publish into a package
index that only your machines can install from. The wheels stay on GitHub — nothing is copied
anywhere — and a small Worker running at Cloudflare's edge holds the GitHub token, checks a
username and password on every request, and turns each download into a short-lived signed URL.
At the end you will install one of your own packages from it with ``pip``, and then make a
release in any repository rebuild it.

Allow about thirty minutes. You do not need to have used Cloudflare before.

This is the guided version of :ref:`howto-private-without-mirroring`, which is where the
reasoning and the failure symptoms are maintained. The steps below build the thing; that page
explains it.

What you will need
==================

* **One or more GitHub repositories whose release process already attaches wheels to their
  Releases**, with at least one release published. This tutorial does not set that up — it
  starts from the releases you already have.
* **Those repositories may be private, and that is the point.** A private repository's release
  assets need an ``Authorization`` header and an ``Accept: application/octet-stream`` header
  that ``pip`` will not send, so an index that merely links to GitHub is useless for them.
  Everything below exists to put something in the serving path that *can* send those headers.
* **A GitHub repository to host the index.** It publishes no packages of its own and serves
  nothing: it holds the configuration file you write in Step 1, and from Step 7 the workflow
  that rebuilds the index. Any repository you can commit to will do, and a dedicated one is the
  tidiest choice: ``gh repo create yourorg/pypi --private --add-readme --clone``. The initial
  commit matters — a repository with no commits has no default branch, and several commands
  below ask for one.
* **A Cloudflare account.** The free plan is enough for everything here.
* `Wrangler <https://developers.cloudflare.com/workers/wrangler/install-and-update/>`_,
  Cloudflare's command line tool, signed in — run ``npx wrangler login`` once. Every
  ``wrangler`` below can be typed as ``npx wrangler`` if you have not installed it globally.
* **A GitHub token that can read release assets in every repository you index** — a
  `fine-grained personal access token
  <https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens>`_
  with **Contents: Read-only** on each one. You will use it twice: once to build the index, and
  once as a Worker secret so the edge can fetch assets on your behalf.
* ``git`` and the `GitHub CLI <https://cli.github.com/>`_, signed in — run ``gh auth login``
  once. `uv <https://docs.astral.sh/uv/getting-started/installation/>`_, and ``python3`` and
  ``curl`` to check the result at the end.
* For the last step only: **ownership of the GitHub organization** holding those repositories.

Work in a checkout of the *index* repository, in one terminal, for the whole tutorial. Nothing
here reads anybody's source code — the checkout is where the configuration file lives, where
the build runs, and, at the end, where the workflow that automates both goes:

.. code-block:: sh

   cd /path/to/your/index/repository
   export INDEX=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
   export BRANCH=$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)
   git switch "$BRANCH"
   export GITHUB_TOKEN=<the token you just created>
   echo "$INDEX on $BRANCH"

Keep this terminal open, and stay on that branch. It is load-bearing twice over: the branch you
deploy from decides whether Cloudflare treats a deployment as production or as a preview
(Step 3), and ``repository_dispatch`` triggers nothing from a workflow file that is not on the
default branch (Step 7).

Step 1 — Turn on redirect mode
==============================

Create ``ghr-pypi.yml`` in the root of the index repository, naming the repositories you want
in the index:

.. code-block:: yaml

   repositories:
     - yourorg/private-lib
     - yourorg/private-app
   title: yourorg internal index
   url: https://ghr-pypi.pages.dev/
   assets: redirect
   target: cloudflare

Two keys do the work, and they go together.

``assets: redirect`` changes what the index links to. In the default mode every link points at
GitHub's own asset URL; here every link points at ``../../_assets/<asset-id>/<filename>`` — a
path on *your* site. Nothing serves those paths yet, which is what the second key is for.

``target: cloudflare`` says what to install at the other end. It writes a Worker that answers
those ``_assets/`` paths by asking GitHub's release asset API for the file, with the token and
the headers ``pip`` cannot send, and returning GitHub's 302 to a signed URL. No package bytes
pass through it.

Both keys need a configuration file. ``assets: redirect`` has no command line form — ``--mirror``
is shorthand for ``assets: mirror`` and there is no equivalent for this mode — and ``--target``
cannot be passed alongside ``--config``.

``url`` is the address this site will have — ``<project>.pages.dev``, for the project you create
in Step 3. Nothing fetches it, and it is not how the index links to its own files: it is what the
landing page prints in the ``pip install`` command a visitor should run, so an index served
somewhere other than it claims still builds and still tells people the wrong thing. If you end
up naming the project something else, change this line and rebuild. ``title`` and ``url`` are
both optional, as is every key except ``repositories``; :ref:`configuration` documents the rest,
and :ref:`config-assets` puts the three asset modes side by side.

Commit it, and tell ``git`` to ignore what the build is about to write beside it:

.. code-block:: sh

   printf 'site/\nwrangler.toml\nSETUP.md\n' >> .gitignore
   git add ghr-pypi.yml .gitignore
   git commit -m "Build a private package index from release assets"
   git push

All three of those are build output, regenerated every time — Step 2 shows what each one is.

Step 2 — Build the site
=======================

.. code-block:: sh

   uvx ghr-pypi index --config ghr-pypi.yml --out site

Four lines come back, one for the index and one for each file the target wrote::

   wrote index for 2 project(s) to site
   wrote site/_worker.js for the cloudflare target
   wrote wrangler.toml for the cloudflare target
   wrote SETUP.md for the cloudflare target

Look at where those three landed, because the split is deliberate:

``site/_worker.js``
   Inside the site. Cloudflare Pages reads a ``_worker.js`` at the root of what you deploy and
   switches to *advanced mode*: every request goes to that code first, and the file itself is
   never served. That is why authentication here gates the whole index and not only downloads.

``wrangler.toml`` and ``SETUP.md``
   Beside the site, not in it — they are operator artifacts. ``--target-out`` defaults to the
   working directory, which here is your checkout, so the site you publish stays exactly the
   files a client should see. A page describing how the index is gated is not one of them,
   which is also why Step 1 kept them out of ``git``.

Both are **regenerated on every build**, so treat them as build output: an edit to either is
lost the next time you run the command above.

``SETUP.md`` is the deployment checklist for *this* index, with your paths already substituted
in. Read it now. Steps 3 to 5 below are the same three commands with the reasoning around them.
They add two flags the checklist does not, both because this tutorial deploys from a git
checkout and a script cannot answer a prompt: ``--production-branch`` in Step 3, and
``--project-name`` on the deploy if you rename the project. On everything else the generated
file wins — it is regenerated with your site, and this page is not.

.. code-block:: sh

   cat SETUP.md

Step 3 — Create the Pages project
=================================

.. code-block:: sh

   wrangler pages project create ghr-pypi --production-branch "$BRANCH"

``ghr-pypi`` is the name the generated ``wrangler.toml`` carries, and the two must agree —
wrangler finds that file by walking up from the working directory, and reads the project name
from it. ``pages.dev`` hostnames are shared by everyone using Cloudflare Pages, so if this one
is taken, pick another name. Changing ``name`` in ``wrangler.toml`` works exactly once: the
file is regenerated on every build, so the rename is gone after the next Step 2. Pass
``--project-name <yours>`` to every ``wrangler pages deploy`` instead — it overrides the file,
survives regeneration, and is the only form that works in the workflow of Step 7.

``--production-branch`` is not decoration either. Leave it off and wrangler asks, defaulting to
the branch you are standing on — or to the literal ``production`` if it cannot detect one at
all, which is what happens outside a checkout. Whatever you answer, a later
``wrangler pages deploy`` from any *other* branch is a **preview** deployment: a different
hostname, a different environment, and none of the secrets Step 4 is about to bind, because
``wrangler pages secret put`` binds to production. That combination is a 401 on a URL you were
not expecting, and Step 5's troubleshooting will send you round in a circle looking for a
missing secret. Name the branch you will actually deploy from, here and in CI.

Every command in this half is ``wrangler pages ...``. The word is load-bearing. This is a
Pages project, not a Worker, and the plain ``wrangler secret`` family would bind to a Worker
instead — leaving this project with no secrets at all, and every request answered ``401``.

Step 4 — Bind the three secrets, before the first deploy
========================================================

.. code-block:: sh

   wrangler pages secret put GHR_PYPI_USER
   wrangler pages secret put GHR_PYPI_PASSWORD
   wrangler pages secret put GITHUB_TOKEN

Each one prompts for its value. The names are exactly what ``_worker.js`` reads, and all three
are required:

``GHR_PYPI_USER`` and ``GHR_PYPI_PASSWORD``
   The credentials clients present. Choose them now; you will put them in ``~/.netrc`` in Step
   6. The Worker **fails closed** — if either is unbound it answers ``401`` to *every* request,
   index pages included, rather than treating an empty value as a credential and publishing a
   private index.

``GITHUB_TOKEN``
   The token from *What you will need*. It never leaves the Worker; clients only ever see a
   302 to a signed URL.

The order of this step and the next is the point of both. Secrets attach to a Pages *project*,
so the project has to exist before there is anything to bind them to — and a Pages deployment
binds its environment when it is created, so a secret added afterwards reaches the *next*
deployment rather than the live one. Cloudflare's own Pages documentation says secrets must be
set "before a deployment that uses those secrets", and that a binding added later needs a
redeploy to take effect.

So: create, bind, deploy. Doing it the other way round publishes a site whose Worker has no
credentials, which then 401s everything, and binding the missing secret afterwards does not
fix it — only another deploy does. **Rotating or adding a secret later is a redeploy too.**
Nothing warns you.

Step 5 — Deploy
===============

.. code-block:: sh

   wrangler pages deploy site

You are standing on ``$BRANCH``, which is the production branch you named in Step 3, so this is
a production deployment — the one the three secrets are bound to. Wrangler prints the site's
address when it finishes. Keep it:

.. code-block:: sh

   export SITE=https://ghr-pypi.pages.dev

Replace that with the address wrangler actually gave you. Every later release is this one
command again and nothing else. A new release never needs a
Worker redeploy: the Worker holds no build data, and reads its allow-list from
``_assets/manifest.json`` in the deployed site at request time. Publishing packages changes the
site, not the code.

Check that the gate is on before you go any further:

.. code-block:: sh

   curl -s -o /dev/null -w '%{http_code}\n' "$SITE/simple/"
   curl -si "$SITE/simple/" | grep -i '^www-authenticate'

.. code-block:: text

   401
   www-authenticate: Basic realm="ghr-pypi", charset="UTF-8"

A 401 on the *index page* is the whole design working: advanced mode routes every request
through the Worker, so the landing page, ``simple/`` and the manifest are behind the same
credentials as the packages. The nginx redirector deliberately does the opposite — it gates
``_assets/`` alone and serves the index pages openly — and
:ref:`howto-private-without-mirroring` weighs the two. If you get a 200 here, the Worker is not
in the deployment. If you
get a 401 you cannot get past in the next step, check in this order: that ``$SITE`` is the
address this deploy printed and not a preview one from another branch (Step 3), and then that
all three secrets are bound and were bound *before* this deployment (Step 4) — a fresh
production deploy that 401s everything is almost always a missing secret, or one bound after
the live deployment was created, rather than a wrong password.

Step 6 — Install from it
========================

``pip`` and ``uv`` both speak Basic auth, and both look up credentials in ``~/.netrc``
(``_netrc`` on Windows). Add an entry for the index host, using the two values you bound in
Step 4:

.. code-block:: text

   machine ghr-pypi.pages.dev
     login <GHR_PYPI_USER>
     password <GHR_PYPI_PASSWORD>

Then install, with the plain URL and no credentials in it:

.. code-block:: sh

   python3 -m venv /tmp/ghr-pypi-check
   /tmp/ghr-pypi-check/bin/pip install --no-deps --index-url "$SITE/simple/" <a project name>

``pip`` reports ``Successfully installed``. Four things happened in that one command: ``pip``
authenticated to the Worker to read the index page, followed a link to ``_assets/`` on the same
host, and the Worker checked the credentials again, looked the asset id up in the manifest, and
handed back GitHub's signed URL, which ``pip`` fetched with no credentials of its own and
verified against the ``#sha256=`` fragment.

``--no-deps`` is there because ``--index-url`` replaces PyPI entirely, so a package with
dependencies has nowhere to resolve them from; :ref:`howto-avoid-pypi` covers using your index
and PyPI together. Clean up:

.. code-block:: sh

   rm -rf /tmp/ghr-pypi-check

.. note::

   **Cloudflare Access is not an alternative to this.** Access authenticates service tokens
   with the ``CF-Access-Client-Id`` and ``CF-Access-Client-Secret`` headers, and ``pip`` cannot
   send arbitrary headers per URL — the same limitation that made this whole mode necessary.
   That is why the Worker does Basic auth itself. Access in front of the site is fine for
   humans browsing the index in a browser; it cannot replace the credentials an installer
   presents.

Step 7 — Rebuild when another repository releases
=================================================

The index you just deployed describes repositories other than the one it lives in, and GitHub
delivers a ``release`` event to the repository the release happened in and nowhere else. So
nothing you have built rebuilds when ``yourorg/private-lib`` publishes. It just quietly keeps
describing yesterday.

If you **own the organization**, one webhook closes that for every repository it holds,
including ones created next year. ``ghr-pypi`` generates the receiver it needs — a standalone
Cloudflare Worker that verifies the delivery's signature and calls GitHub's dispatch API
against your index repository:

.. code-block:: sh

   uvx ghr-pypi webhook --index-repo "$INDEX" --out hook
   printf 'hook/\n' >> .gitignore
   cd hook

That writes ``worker.js``, ``wrangler.toml`` and a ``SETUP.md`` of its own — build output like
everything in Step 2, which is why the whole directory joins the ignore list. Step 1's entries
would only have caught two of the three: they match by name, wherever the file sits, and
``worker.js`` is not among them. **Follow the generated** ``SETUP.md``, not this page — it is
regenerated with your repository already substituted in. In outline it is two commands, in this
order:

.. code-block:: sh

   wrangler deploy
   wrangler secret put WEBHOOK_SECRET
   wrangler secret put GITHUB_TOKEN

**That order is the inverse of Steps 3 to 5, and the reason is different in each direction.** A
Worker takes its secrets after deployment because ``wrangler secret put`` publishes a new
version immediately — no redeploy needed, then or when you rotate one. The danger in the other
order is worse than a wasted step: ``wrangler secret put`` against a Worker that does not exist
offers to create one, and a non-interactive shell accepts silently, publishing a placeholder
under the name you meant to use. A Pages project is the opposite on both counts, which is why
Step 4 came before Step 5. Note also that these are plain ``wrangler ...`` commands, with no
``pages``: a Worker, not a Pages project.

Between the two commands the receiver answers every request ``500 Receiver is not configured``,
which is safe — it is failing closed, and nothing points at it yet.

Then create the webhook, in the organization's **Settings → Webhooks**, with the payload URL
the deploy printed, content type ``application/json``, the same ``WEBHOOK_SECRET`` value, and
**Releases** as the only event. The generated ``SETUP.md`` has the form field by field.

.. note::

   **An organization webhook requires being an organization owner**, which is a strictly higher
   bar than admin on a repository — being an admin of all of an organization's repositories
   does not grant it. If you are not an owner, do not deploy this: you are on a different row
   of :ref:`howto-rebuild-on-release`, where each releasing repository's own workflow makes the
   dispatch call instead.

The receiver's last hop is ``POST /repos/$INDEX/dispatches``, so the index repository — the one
you have been working in all along — has to be listening for it. Go back up to its root
(``cd ..``); the rest of this step is there, not in ``hook/``. Its workflow needs this trigger,
on a file committed to the **default branch**, because ``repository_dispatch`` fires nothing
from any other:

.. code-block:: yaml

   on:
     repository_dispatch:
       types: [ghr-pypi-rebuild]

That workflow runs the two commands you ran by hand — the ``ghr-pypi index`` of Step 2 and the
``wrangler pages deploy`` of Step 5 — so it needs three secrets. These are **GitHub Actions
repository secrets**, added under the index repository's **Settings → Secrets and variables →
Actions**: a different store from the Pages secrets of Step 4, which only the Worker can read.
One of them holds the same GitHub token, this time so the build can read the releases — the
workflow's built-in ``${{ github.token }}`` will not do, because it grants nothing outside the
repository it runs in. The deploy needs **both** ``CLOUDFLARE_API_TOKEN`` and
``CLOUDFLARE_ACCOUNT_ID``: with only the token, wrangler has to enumerate the accounts it can
see and cannot choose between them with nobody there to ask. The API token needs one
permission, **Account → Cloudflare Pages → Edit**; both are described under `direct upload with
continuous integration
<https://developers.cloudflare.com/pages/how-to/use-direct-upload-with-continuous-integration/>`_.
Give that deploy ``--project-name`` explicitly, per Step 3, since the ``wrangler.toml`` the
build has just regenerated may not name your project.

Until that workflow exists, every hop still reports success and nothing rebuilds: GitHub's
dispatch endpoint answers the Worker ``204``, the Worker answers GitHub ``202``, and the
delivery is green. Neither number says whether a workflow was listening.

:ref:`howto-rebuild-on-release` is the full treatment — the three routes, which one your
permissions put you on, and why a spurious rebuild costs nothing.

What you built
==============

A private :pep:`503` and :pep:`691` index served from Cloudflare's edge, where **no package
bytes were copied anywhere**. The wheels are still release assets on the repositories that made
them; the site is a few kilobytes of index pages plus an allow-list; and between a client and a
download sits a Worker that checks a username and password, holds the only copy of the GitHub
token, and hands back a signed URL that expires. The repositories can be private, which an
index that merely links to GitHub cannot manage at all.

Publishing a release is one ``wrangler pages deploy`` of a freshly built site, or nothing at
all if you finished Step 7.

Where to go next
================

* :ref:`howto-private-without-mirroring` — the same deployment as a reference: every failure
  symptom, what the manifest discloses, and why the allow-list is what makes the redirector
  safe to expose.
* :ref:`howto-rebuild-on-release` — the three ways to rebuild on a release elsewhere, and
  :ref:`cli-webhook` for every file the receiver command writes.
* :ref:`howto-private-repository` — mirroring, the other answer to private packages: it copies
  the wheels into the site and needs no token at request time.
* :ref:`howto-customize-pages` — the landing page, and the response headers each mode sets.
* :ref:`targets` — what each target emits in each mode, and how to add one of your own.
* :ref:`configuration` and :ref:`cli` document every key, option and error message.
