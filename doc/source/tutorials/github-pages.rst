.. include:: ../refs.rst

.. _tutorial-github-pages:

=======================
Publish on GitHub Pages
=======================

In this tutorial you will turn the releases your repository already publishes into a real
package index, deploy it to GitHub Pages, and install one of your own packages from it with
``pip``. Nothing runs on a server you own, nothing costs anything, and your release process is
not changed: one GitHub Actions workflow reads the repository's releases, writes the index,
and deploys it. Allow about ten minutes.

What you will need
==================

* **A GitHub repository whose release process already attaches wheels to its Releases**, with
  at least one such release published. This tutorial does not set that up — it starts from the
  releases you already have.
* **That repository must be public.** The index you build here *links* to release assets on
  GitHub, and a private repository's assets need an ``Authorization`` header ``pip`` will not
  send. Every step below would still succeed — the failure lands on the last one, downloading
  the file rather than reading the index. GitHub Pages on a private repository also requires a
  paid plan. If yours is private, read :ref:`howto-private-repository` first: the answer is a
  different kind of index, not a different workflow.
* Permission to add a workflow to that repository and to change its settings.
* The `GitHub CLI <https://cli.github.com/>`_, signed in — run ``gh auth login`` once.
* ``git`` and ``curl``, and ``python3`` to check the result at the end.

Work in a checkout of that repository, in one terminal, for the whole tutorial. Every command
below names the repository by owner and by name, so set both once:

.. code-block:: sh

   cd /path/to/your/repository
   export OWNER=$(gh repo view --json owner --jq .owner.login)
   export NAME=$(gh repo view --json name --jq .name)
   echo "$OWNER/$NAME"

That should print the repository you mean. Keep this terminal open.

Step 1 — Add the Pages workflow
===============================

This workflow reads the repository's releases, writes a :pep:`503` index into ``_site/``, and
publishes that directory to GitHub Pages. Create ``.github/workflows/pages.yml``:

.. code-block:: yaml

   name: pages

   on:
     release:
       types: [published, deleted]
     workflow_dispatch:

   permissions: {}

   concurrency:
     group: pages
     cancel-in-progress: true

   jobs:
     build:
       runs-on: ubuntu-latest
       permissions:
         contents: read # read the repository's releases
       steps:
         - uses: astral-sh/setup-uv@v9

         - name: Build the package index
           env:
             GITHUB_TOKEN: ${{ github.token }}
           run: uvx ghr-pypi index

         - uses: actions/configure-pages@v6

         - uses: actions/upload-pages-artifact@v5

     deploy:
       needs: build
       runs-on: ubuntu-latest
       permissions:
         pages: write # deploy the built site
         id-token: write # authenticate the deployment
       environment:
         name: github-pages
         url: ${{ steps.deployment.outputs.page_url }}
       steps:
         - id: deployment
           uses: actions/deploy-pages@v5

Four things about this file are worth noticing.

The build step passes ``index`` and nothing else. ``index`` takes the repository to index from
``GITHUB_REPOSITORY``, which Actions sets for every step, and writes the site to ``_site`` —
which is also the directory ``actions/upload-pages-artifact`` uploads when it is given no
``path``. Naming either one explicitly
(``ghr-pypi index "$GITHUB_REPOSITORY" --out site``, with a matching ``path: site``) still
works and is what you would do outside Actions.

The build job never checks the repository out. It does not need to: ``ghr-pypi`` reads the
releases through GitHub's API, so the only inputs are the repository name and a token.

``GITHUB_TOKEN`` is the workflow's own automatically provided token. ``ghr-pypi index``
always requires a token, even for a public repository, because unauthenticated API requests
are rate limited far too aggressively to build an index with. ``contents: read`` is all it
needs for the repository the workflow runs in. See `automatic token authentication
<https://docs.github.com/en/actions/tutorials/authenticate-with-github_token>`_.

The workflow only runs on a release or on an explicit dispatch. It deliberately does not run
on every push, because ``ghr-pypi index`` refuses to write an empty index — a run before your
first release would fail.

.. note::

   If your releases are created by a workflow using the built-in ``GITHUB_TOKEN`` — anything
   built on ``gh release create`` or ``softprops/action-gh-release`` — the ``release`` trigger
   above will never fire. GitHub suppresses events raised by that token so that workflows
   cannot trigger themselves in a loop; see `events that trigger workflows
   <https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows>`_.
   The fix is one step at the end of the release workflow you already have, after the release
   exists, with ``actions: write`` added to that job's permissions:

   .. code-block:: yaml

      - name: Rebuild the package index
        env:
          GH_TOKEN: ${{ github.token }}
          GH_REPO: ${{ github.repository }}
        run: gh workflow run pages.yml

   A release published by hand fires the event normally and needs none of this.

Commit the workflow onto the repository's **default branch** — ``workflow_dispatch`` does not
offer a workflow that lives only on some other branch:

.. code-block:: sh

   git switch "$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)"
   git add .github/workflows/pages.yml
   git commit -m "Build a package index from the releases"
   git push

Step 2 — Turn on GitHub Pages
=============================

Open the repository's settings page:

.. code-block:: sh

   echo "https://github.com/$OWNER/$NAME/settings/pages"

In **Build and deployment**, set **Source** to **GitHub Actions**. There is nothing to save;
the choice takes effect immediately, and the setting is described under `configuring a
publishing source
<https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site>`_.
Do this before the next step: ``actions/configure-pages`` fails with a clear error if Pages
has not been enabled for the repository.

Step 3 — Build the index
========================

You already have the releases, so there is no need to publish a new one to see an index.
Dispatch the workflow by hand, this once, and follow the run:

.. code-block:: sh

   gh workflow run pages.yml
   sleep 5
   gh run watch "$(gh run list --workflow pages.yml --limit 1 \
     --json databaseId --jq '.[0].databaseId')"

From here on it runs itself, whenever a release is published or deleted, subject to the note
above. When the run reports success, read what the index build actually said:

.. code-block:: sh

   gh run view --log "$(gh run list --workflow pages.yml --limit 1 \
     --json databaseId --jq '.[0].databaseId')" | grep -E 'wrote index|warning:'

The lines come back prefixed by ``gh`` with their job and step name. The result counts the
distinct projects your releases add up to, which for most repositories is one::

   wrote index for 1 project(s) to _site

A warning may follow it, one per repository indexed::

   warning: <owner>/<name>: 4 of 4 wheels have no .metadata asset; resolvers must download
   full wheels for dependency metadata

That is expected and harmless here. It means installers cannot read a package's dependency
list without downloading the wheel itself; :ref:`config-metadata` explains what to do about it
later.

Step 4 — Look at what you built
===============================

Print the address of your index and open it in a browser:

.. code-block:: sh

   echo "https://$OWNER.github.io/$NAME/"

The landing page lists the projects in the index and shows the install command. The first
deployment of a brand-new Pages site can take a minute or two to become reachable; if you get
a 404, wait and reload. Pick one of the projects it lists and keep its name to hand:

.. code-block:: sh

   export PKG=<a project name from that page>

The index itself lives one level down. These are the pages ``pip`` actually reads:

.. code-block:: sh

   curl -s "https://$OWNER.github.io/$NAME/simple/$PKG/" | head -20

Each link ends with a ``#sha256=...`` fragment. That digest comes from GitHub's release asset
API and is what makes the download verifiable: ``pip`` computes the hash of what it received
and refuses the file if it does not match.

Step 5 — Install your package from your index
=============================================

This is the point of the whole exercise. Make a throwaway virtual environment and install from
the index you just deployed:

.. code-block:: sh

   python3 -m venv /tmp/ghr-pypi-check
   /tmp/ghr-pypi-check/bin/pip install --no-deps \
     --index-url "https://$OWNER.github.io/$NAME/simple/" \
     "$PKG"

``pip`` reports ``Successfully installed`` with your package's name and version. Nothing in
that install came from PyPI: ``--index-url`` replaced PyPI entirely, ``pip`` read your
``simple/`` pages, followed the link to the release asset on GitHub, and verified the sha256.

``--no-deps`` is there because that replacement is total. Your index holds your own releases
and nothing else, so a package with dependencies has nowhere to resolve them from;
:ref:`howto-avoid-pypi` covers installing from your index and PyPI together.

Clean up the throwaway environment:

.. code-block:: sh

   rm -rf /tmp/ghr-pypi-check

What you built
==============

A repository that, every time you publish a release, regenerates a :pep:`503` and :pep:`691`
index from every release it has ever made and deploys that index to GitHub Pages. The packages
stay exactly where they already were — release assets, served by GitHub's own CDN — and the
index is a directory of static files with no server, no database, and no credentials sitting
anywhere. Publish the next release the way you always have and the index follows it — on the
``release`` trigger, or on the dispatch step from Step 1 if a workflow is what publishes it.

Where to go next
================

* The :ref:`how-to guides <how-to>` answer the questions that come next: aggregating several
  repositories into one index, indexing a private repository, customizing the landing page.
* :ref:`configuration` documents every key of the YAML configuration file, which is how you
  set the title, the URL, and everything else the command line form leaves at its default.
* :ref:`cli` documents every command line option, every exit code, and every error message.
* The tool itself lives at repo_.
