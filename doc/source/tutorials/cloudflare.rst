.. include:: ../refs.rst

.. _tutorial-cloudflare:

=====================
Publish on Cloudflare
=====================

In this tutorial you will write a small Python package, publish it as a GitHub release, and
build a package index that Cloudflare Pages rebuilds and serves for you on its global network.
Unlike an index that links back to GitHub, this one is *mirrored*: the wheels themselves are
copied into the site, so Cloudflare serves the packages as well as the index and GitHub is out
of the download path entirely. At the end you will install your package from it with ``pip``.

Allow about thirty minutes. You do not need to have used Cloudflare before. Follow the steps
in order, type every command exactly as it is written, and at the end you will have installed
a package from an index you built yourself.

What you will need
==================

* A GitHub account.
* A Cloudflare account. The free plan is enough for everything here.
* ``git``.
* The `GitHub CLI <https://cli.github.com/>`_, signed in — run ``gh auth login`` once.
* `uv <https://docs.astral.sh/uv/getting-started/installation/>`_.
* ``python3``, to check the result at the end.

Every command below refers to your GitHub username. Set it once, in the terminal you will use
for the whole tutorial:

.. code-block:: sh

   export OWNER=$(gh api user --jq .login)
   echo "$OWNER"

That should print your username. Keep this terminal open.

Step 1 — Create the package
===========================

Make a directory, start a git repository in it, and create the package layout:

.. code-block:: sh

   mkdir hello-index
   cd hello-index
   git init -b main
   mkdir -p src/hello_index .github/workflows

Create ``pyproject.toml`` with exactly this content:

.. code-block:: toml

   [build-system]
   requires = ["hatchling"]
   build-backend = "hatchling.build"

   [project]
   name = "hello-index"
   version = "1.0.0"
   description = "A package installed from a GitHub release asset"
   requires-python = ">=3.9"

   [tool.hatch.build.targets.wheel]
   packages = ["src/hello_index"]

Create ``src/hello_index/__init__.py``:

.. code-block:: python

   """A very small package, published from a GitHub release asset."""

   __version__ = "1.0.0"


   def greet(source: str) -> str:
       """Return a greeting naming where this package was installed from."""
       return f"Hello from {source}!"

Step 2 — Add the build script and the caching rules
===================================================

Cloudflare will build the index itself, on its own machines, every time the project is
deployed. Give it a script to run. Create ``build.sh`` in the project root:

.. code-block:: sh

   #!/bin/sh
   set -eu

   # Cloudflare's build image has no uv; fetch it, then run ghr-pypi with it
   # without installing anything permanently.
   curl -LsSf https://astral.sh/uv/install.sh | sh
   "$HOME/.local/bin/uvx" ghr-pypi index "$REPO" --out site --mirror

   # _headers only takes effect from the root of the published directory.
   cp _headers site/

``$REPO`` is an environment variable you will set in the Cloudflare dashboard in step 6, so
this script has nothing repository-specific baked into it. ``--mirror`` is what makes the site
self-contained: ``ghr-pypi`` downloads every release asset into ``site/files/`` and rewrites
the index links to point at those copies.

Now the caching rules. Cloudflare Pages reads a file named ``_headers`` from the root of the
directory it publishes and turns each rule into response headers. Create ``_headers`` in the
project root:

.. code-block:: text

   /files/*
     Cache-Control: public, max-age=31536000, immutable

   /simple/*
     Cache-Control: public, max-age=300

The two rules say opposite things on purpose. ``/files/`` holds the mirrored wheels and
sdists; a released file never changes, so it can be cached forever. ``/simple/`` holds the
index pages, which gain a new entry every time you publish; five minutes keeps them fresh
without asking the origin on every install. The full syntax is documented under `_headers
<https://developers.cloudflare.com/pages/configuration/headers/>`_.

.. note::

   You are writing ``_headers`` by hand here so that you can see what each rule does.
   ``ghr-pypi`` can now generate it instead: adding ``--target cloudflare`` to the build
   command writes these same cache rules into ``site/_headers``, plus a content type for the
   ``.metadata`` sidecars. See :ref:`targets`.

Step 3 — Push the repository to GitHub
======================================

.. code-block:: sh

   git add .
   git commit -m "hello-index 1.0.0"
   gh repo create hello-index --public --source=. --push

Step 4 — Publish the first release
==================================

Build the distributions and attach them to a GitHub release:

.. code-block:: sh

   uv build
   gh release create v1.0.0 dist/* --title "v1.0.0" --notes "First release"

Check that the release has both assets:

.. code-block:: sh

   gh release view v1.0.0

The ``ASSETS`` section lists ``hello_index-1.0.0-py3-none-any.whl`` and
``hello_index-1.0.0.tar.gz``. Those two files are what the index will be built from.

Step 5 — Create a token for Cloudflare
======================================

Cloudflare's build machines are not GitHub, so they have no automatic GitHub token. You have
to give them one. ``ghr-pypi`` always needs a token — even for a public repository —
because unauthenticated GitHub API requests are rate limited far too aggressively to build an
index with.

Open the fine-grained token page:

.. code-block:: sh

   echo "https://github.com/settings/personal-access-tokens/new"

Fill it in like this:

* **Token name**: ``cloudflare-hello-index``
* **Expiration**: 90 days
* **Repository access**: *Only select repositories* → ``hello-index``
* **Permissions** → **Repository permissions** → **Contents**: *Read-only*

Click **Generate token** and copy the value. You will paste it into Cloudflare in the next
step and you will not be able to read it again afterwards. Background on these tokens is in
`managing your personal access tokens
<https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens>`_.

Step 6 — Create the Cloudflare Pages project
============================================

Sign in to the Cloudflare dashboard at ``dash.cloudflare.com``, go to **Workers & Pages**,
choose **Create**, open the **Pages** tab, and choose **Connect to Git**. Authorize Cloudflare
to read your GitHub account if it asks, select the ``hello-index`` repository, and continue to
the build settings. The same walkthrough with screenshots is in `git integration
<https://developers.cloudflare.com/pages/get-started/git-integration/>`_.

Fill the build settings in exactly like this:

**Project name**
   ``hello-index-`` followed by your GitHub username, for example ``hello-index-octocat``.
   The project name becomes the hostname ``<project-name>.pages.dev``, and those hostnames are
   shared by everyone using Cloudflare Pages, so a plain ``hello-index`` may already be taken.

**Production branch**
   ``main``

**Framework preset**
   ``None``

**Build command**
   ``sh build.sh``

**Build output directory**
   ``site``

Then open **Environment variables** and add two, both for the **Production** environment:

.. list-table::
   :header-rows: 1
   :widths: 24 40 36

   * - Name
     - Value
     - Type
   * - ``GITHUB_TOKEN``
     - the token you copied in step 5
     - **Encrypt**
   * - ``REPO``
     - ``<your username>/hello-index``
     - Plaintext

Click **Encrypt** on ``GITHUB_TOKEN`` before saving. An encrypted variable is write-only
afterwards: the build can read it, the dashboard cannot show it to you again.

Press **Save and Deploy**. The build settings can be changed later under
**Settings → Builds**; they are described under `build configuration
<https://developers.cloudflare.com/pages/configuration/build-configuration/>`_.

Step 7 — Watch the first deployment
===================================

Cloudflare shows the build log as it runs. It takes a couple of minutes, most of it spent
downloading ``uv``. The interesting part is at the end::

   wrote index for 1 project(s) to site

Above that line, ``ghr-pypi`` reports each file it mirrored. Because ``--mirror`` was used, it
downloaded both release assets into ``site/files/hello-index/``, hashed them as they streamed
to disk, extracted each wheel's :pep:`658` core metadata, and rewrote every link in the index
to point at those local copies instead of at GitHub.

When the deployment finishes, the page shows the site's address. Copy it and put it in your
terminal:

.. code-block:: sh

   export SITE=https://hello-index-octocat.pages.dev
   echo "$SITE"

Replace the example with the address Cloudflare actually gave you.

Step 8 — Look at what you built
===============================

Open ``$SITE`` in a browser. The landing page lists the projects in the index. The pages
``pip`` reads are one level down:

.. code-block:: sh

   curl -s "$SITE/simple/hello-index/" | head -20

Every link is relative — ``../../files/hello-index/hello_index-1.0.0-py3-none-any.whl`` —
and carries a ``#sha256=`` fragment computed from the bytes Cloudflare is actually serving.
Check that the caching rules arrived:

.. code-block:: sh

   curl -sI "$SITE/files/hello-index/hello_index-1.0.0-py3-none-any.whl" \
     | grep -i '^cache-control'

.. code-block:: text

   cache-control: public, max-age=31536000, immutable

Step 9 — Install your package from your index
=============================================

This is the point of the whole exercise. Make a throwaway virtual environment and install
from the index Cloudflare is serving:

.. code-block:: sh

   python3 -m venv /tmp/hello-index-check
   /tmp/hello-index-check/bin/pip install --index-url "$SITE/simple/" hello-index

``pip`` reports::

   Successfully installed hello-index-1.0.0

Nothing in that install touched PyPI and nothing touched GitHub. ``--index-url`` replaced
PyPI, ``pip`` read your ``simple/`` pages from Cloudflare, downloaded the wheel from
Cloudflare, and verified the sha256. Prove the package works:

.. code-block:: sh

   /tmp/hello-index-check/bin/python -c \
     "import hello_index; print(hello_index.greet('Cloudflare'))"

.. code-block:: text

   Hello from Cloudflare!

Clean up the throwaway environment:

.. code-block:: sh

   rm -rf /tmp/hello-index-check

Step 10 — Rebuild the index on every release
============================================

Cloudflare rebuilds when you push a commit. But publishing a release does not push a commit,
so right now a new release would not reach the index until you happened to change the code.
Wire the two together with a deploy hook.

In the Cloudflare dashboard, open your project, go to **Settings → Builds → Deploy hooks**
and add one: name it ``new-release``, set the branch to ``main``, and copy the URL it gives
you. It is a secret — anyone holding it can start a build. Deploy hooks are documented under
`deploy hooks <https://developers.cloudflare.com/pages/configuration/deploy-hooks/>`_.

Store it as a repository secret:

.. code-block:: sh

   gh secret set CLOUDFLARE_DEPLOY_HOOK

Paste the URL when prompted. Then create ``.github/workflows/rebuild-index.yml``:

.. code-block:: yaml

   name: rebuild-index

   on:
     release:
       types: [published, deleted]

   permissions: {}

   jobs:
     rebuild:
       runs-on: ubuntu-latest
       steps:
         - name: Ask Cloudflare Pages to rebuild the index
           env:
             DEPLOY_HOOK: ${{ secrets.CLOUDFLARE_DEPLOY_HOOK }}
           run: curl -fsS -X POST "$DEPLOY_HOOK"

Commit and push it:

.. code-block:: sh

   git add .github/workflows/rebuild-index.yml
   git commit -m "Rebuild the index when a release is published"
   git push

Now publish a second version and watch it flow through. Change ``version = "1.0.0"`` to
``version = "1.0.1"`` in ``pyproject.toml`` and ``__version__`` to ``"1.0.1"`` in
``src/hello_index/__init__.py``, then:

.. code-block:: sh

   git commit -am "hello-index 1.0.1"
   git push
   rm -rf dist
   uv build
   gh release create v1.0.1 dist/* --title "v1.0.1" --notes "Second release"

The release fires the workflow, the workflow calls the deploy hook, and Cloudflare rebuilds.
Once the new deployment is live:

.. code-block:: sh

   python3 -m venv /tmp/hello-index-check
   /tmp/hello-index-check/bin/pip install --index-url "$SITE/simple/" hello-index
   /tmp/hello-index-check/bin/pip show hello-index | head -2
   rm -rf /tmp/hello-index-check

``pip`` picks 1.0.1, because that is now the newest version your index advertises.

.. note::

   You created that release from your own machine with your own credentials, so GitHub fired
   the ``release`` event and the workflow ran. Releases created *by a workflow* using the
   built-in ``GITHUB_TOKEN`` do not fire ``release`` events — GitHub suppresses them so
   workflows cannot trigger themselves in a loop. If you later move release creation into CI,
   call the deploy hook from that same job instead of relying on the event.

What you built
==============

A repository whose releases are mirrored onto Cloudflare's network as a self-contained
:pep:`503` and :pep:`691` index: the index pages, the wheels, the sdists, and the :pep:`658`
metadata are all served from one origin, with cache lifetimes that match how each kind of file
behaves. Publishing a release rebuilds it automatically. There is no server to patch and no
package storage to pay for.

What Workers would add
======================

Everything above is static files. That is a deliberate limit, and it is worth knowing what
sits just past it, because Cloudflare's `Workers <https://developers.cloudflare.com/workers/>`_
and `Pages Functions <https://developers.cloudflare.com/pages/functions/>`_ run code at the
same edge that serves this site.

Two things become possible with a few lines of code, both beyond the scope of this tutorial:

* **Content negotiation for the JSON API.** ``ghr-pypi`` writes both the HTML index and the
  :pep:`691` JSON index; on a static host the JSON sits at ``index.json`` alongside the HTML.
  A Function could inspect the request's ``Accept`` header and return the JSON body with the
  ``application/vnd.pypi.simple.v1+json`` media type at the canonical URL, which is what
  :pep:`691` describes. The :ref:`nginx tutorial <tutorial-nginx>` does exactly this with
  configuration instead of code.

* **Access control.** A Function can check an ``Authorization`` header before serving anything
  under ``/files/``, turning a mirrored private index into one that only your machines can
  install from.

Where to go next
================

* The :ref:`how-to guides <how-to>` answer the questions that come next: aggregating several
  repositories into one index, indexing a private repository, customizing the landing page.
* :ref:`config-assets` explains exactly what mirroring does, how downloads are verified, and
  what it does not clean up.
* :ref:`configuration` documents every key of the YAML configuration file, which is how you
  set the title, the URL, and everything else the command line form leaves at its default.
* :ref:`cli` documents every command line option, every exit code, and every error message.
* The tool itself lives at repo_.
