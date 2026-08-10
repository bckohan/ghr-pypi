.. include:: ../refs.rst

.. _tutorial-other-repositories:

========================
Index other repositories
========================

In this tutorial you will build one package index out of the releases of *several* repositories,
none of which has to be the repository the index lives in. You will start from a list of two,
replace that list with a pattern matching a whole owner, subtract the repositories you do not
want, and install one of the packages from the result. Allow about fifteen minutes.

That is a different model from an index that describes its own repository, not a variation on
it. The repository you work in here publishes no packages of its own: it holds a workflow, a
configuration file, and the site. What it serves belongs to other repositories entirely.
Everything that follows — the config file, the token, the pattern — comes out of that shift.

What you will need
==================

* **Two or more GitHub repositories whose release process already attaches wheels to their
  Releases**, with at least one release published in each, owned by the same user or
  organization. This tutorial does not set that up — it starts from the releases you already
  have.
* **Those repositories must be public** for the last step to work. The index you build here
  *links* to release assets on GitHub, and a private repository's assets need an
  ``Authorization`` header ``pip`` will not send. If yours are private, read
  :ref:`howto-private-repository` first: the answer is a different kind of index, not a
  different config file.
* **A repository to host the index.** Any repository you can add a workflow to and turn Pages on
  for will do — it does not have to be one of the ones you are indexing, and a dedicated one is
  the tidiest choice: ``gh repo create yourorg/pypi --public --add-readme --clone``. The initial
  commit matters: a repository with no commits has no default branch, and two commands below ask
  for one. GitHub Pages on a private repository requires a paid plan.
* The `GitHub CLI <https://cli.github.com/>`_, signed in — run ``gh auth login`` once.
* `uv <https://docs.astral.sh/uv/>`_, to try a configuration change before pushing it.
* ``git``, and ``python3`` to check the result at the end.

Work in a checkout of the repository that will *host* the index, in one terminal, for the whole
tutorial:

.. code-block:: sh

   cd /path/to/your/index/repository
   export OWNER=$(gh repo view --json owner --jq .owner.login)
   export NAME=$(gh repo view --json name --jq .name)
   echo "https://$OWNER.github.io/$NAME/"

That prints the address the finished index will have. Keep this terminal open.

Step 1 — Write a configuration file
===================================

The command line form of ``ghr-pypi index`` indexes one repository — the one named in
``$GITHUB_REPOSITORY``, which GitHub Actions sets to the repository the workflow runs in. As
soon as the index describes something else, the list of repositories has to be written down
somewhere, and that somewhere is a YAML file checked in beside the workflow.

Create ``ghr-pypi.yml`` in the root of the index repository, naming two of your repositories:

.. code-block:: yaml

   repositories:
     - yourorg/lib-one
     - yourorg/lib-two
   title: yourorg package index
   url: https://yourorg.github.io/pypi/

Substitute your own owner and repository names, and set ``url`` to the address printed above.
Nothing fetches that URL; it is what the landing page uses to write the ``pip install`` command
a visitor should run, so an index served from somewhere other than it claims still builds, and
still tells people the wrong thing.

``title`` and ``url`` are optional and every other key has a default, so ``repositories`` alone
is a working file. :ref:`configuration` documents the rest.

Step 2 — Point the workflow at it
=================================

Create ``.github/workflows/pages.yml``:

.. code-block:: yaml

   name: pages

   on:
     workflow_dispatch:

   permissions: {}

   concurrency:
     group: pages
     cancel-in-progress: true

   jobs:
     build:
       runs-on: ubuntu-latest
       permissions:
         contents: read # check this repository out, and read the listed repositories
       steps:
         - uses: actions/checkout@v7
           with:
             persist-credentials: false # the build needs the file, not a git credential

         - uses: astral-sh/setup-uv@v9

         - name: Build the package index
           env:
             GITHUB_TOKEN: ${{ github.token }}
           run: uvx ghr-pypi index --config ghr-pypi.yml

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

Three things differ from the zero-configuration form. Two of them follow from the config file
existing: the build passes ``--config ghr-pypi.yml``, and the job checks the repository out —
``ghr-pypi`` reads the releases through GitHub's API and needs no source code, but it does need
that one file on disk. The third is the trigger, below.

The repositories go in the file and nowhere else. Naming them on the command line *as well* is
rejected before anything is fetched::

   error: with --config, list repositories in the config file

There is one trigger, ``workflow_dispatch``, and that is not an oversight. GitHub delivers a
``release`` event to the repository the release happened in and nowhere else, so no release in
``yourorg/lib-one`` will ever reach this workflow. Closing that gap is a subject of its own; it
is the first link under `Where to go next`_.

The token bounds what the index can see
---------------------------------------

``${{ github.token }}`` is the workflow's own automatically provided token, and ``ghr-pypi
index`` always requires a token — even for public repositories, because unauthenticated API
requests are rate limited far too aggressively to build an index with. What that particular
token *grants*, though, stops at the repository the workflow runs in. It reads the repositories
listed above only because they are public, and anyone's token can read a public repository.

.. note::

   A private repository elsewhere is not readable with the built-in token, and how that fails
   depends on how you named it. Listed explicitly, the build stops::

      error: GitHub API request for yourorg/private-lib failed: HTTP Error 404: Not Found

   Reached by a pattern — Step 4 — it is **silently missing** instead. Expansion lists the
   repositories the token can see, a private one is not in that listing, and the build succeeds
   with a shorter index. Nothing in the log says it is short.

So an index that covers private repositories needs a token that can read them: a `fine-grained
personal access token
<https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens>`_
or a GitHub App installation token with **Contents: Read-only** on each one, stored as a
repository secret and passed as ``GHR_PYPI_TOKEN: ${{ secrets.INDEX_TOKEN }}`` in place of
the ``GITHUB_TOKEN: ${{ github.token }}`` line. Everything in this tutorial is public, so the
built-in token is enough here.

Commit the two files onto the index repository's **default branch** — ``workflow_dispatch`` does
not offer a workflow that lives only on some other branch:

.. code-block:: sh

   git switch "$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)"
   git add ghr-pypi.yml .github/workflows/pages.yml
   git commit -m "Build a package index from the releases of other repositories"
   git push

Step 3 — Turn Pages on and build
================================

Open the index repository's settings page:

.. code-block:: sh

   echo "https://github.com/$OWNER/$NAME/settings/pages"

In **Build and deployment**, set **Source** to **GitHub Actions**. There is nothing to save; the
choice takes effect immediately. Do this before the next command: ``actions/configure-pages``
fails with a clear error if Pages has not been enabled for the repository.

Then dispatch the workflow and follow the run:

.. code-block:: sh

   gh workflow run pages.yml
   sleep 5
   gh run watch "$(gh run list --workflow pages.yml --limit 1 \
     --json databaseId --jq '.[0].databaseId')"

When it reports success, read what the build actually said:

.. code-block:: sh

   gh run view --log "$(gh run list --workflow pages.yml --limit 1 \
     --json databaseId --jq '.[0].databaseId')" | grep -E 'wrote index|warning:'

The lines come back prefixed by ``gh`` with their job and step name. The count is of distinct
*projects*, not repositories — two repositories publishing the same project name share one page,
with their files merged::

   wrote index for 2 project(s) to _site

A warning may follow, one per repository indexed::

   warning: yourorg/lib-one: 4 of 4 wheels have no .metadata asset; resolvers must download
   full wheels for dependency metadata

That is expected and harmless here; :ref:`config-metadata` explains what to do about it later.

Step 4 — Match a whole owner with a pattern
===========================================

Two repositories are a list. Twenty are a pattern. The **name** half of a ``repositories`` entry
may be an ``fnmatch`` pattern, so replace the list with one line:

.. code-block:: yaml

   repositories:
     - yourorg/*
   title: yourorg package index
   url: https://yourorg.github.io/pypi/

``*``, ``?`` and ``[seq]`` all work and matching ignores case, so ``yourorg/lib-*`` takes a
family. Only the name half may be a pattern; ``*/lib`` is rejected before any request is made.

Rather than push and wait, run the build locally to see what the pattern reaches. It writes to a
throwaway directory and touches nothing on GitHub:

.. code-block:: sh

   GHR_PYPI_TOKEN=$(gh auth token) uvx ghr-pypi index --config ghr-pypi.yml --out /tmp/ghr-pypi-try

Each pattern reports on stderr how many repositories it added::

   expanded 'yourorg/*' to 23 repositories

**That number is a property of the token, not of the owner.** Expansion lists what the
credential can see, so one line means different things to different tokens. The command above
ran under *your* account; the workflow will run under its built-in one. Against an organization
you belong to, yours may reach private repositories the workflow's cannot, and the two counts
differ; against a personal account they agree whoever's token it is, because the listing GitHub
offers for a user is public-only for everybody (:ref:`howto-org-user-accounts`). You will see
which case you are in at the end of the next step.

Everything the token can see is a candidate, **forks and archived repositories included** —
there is no hidden filter, which is why the next step exists. Matches are sorted and spliced in
where the pattern stood, and that ordering is load-bearing: when two repositories publish the
same *filename*, the one indexed first wins and the other is dropped with a warning. Listing a
repository above a pattern is how you make its copy the winning one.

Step 5 — Exclude what you do not want
=====================================

Twenty-three repositories is more than you meant. ``exclude_repositories`` subtracts from what
the patterns brought in:

.. code-block:: yaml

   repositories:
     - yourorg/*
   exclude_repositories:
     - yourorg/*-internal
     - yourorg/abandoned-fork
   title: yourorg package index
   url: https://yourorg.github.io/pypi/

Run the local build again and watch the count fall::

   expanded 'yourorg/*' to 21 repositories

It subtracts from **expansions only**. A repository named explicitly in ``repositories`` is
always indexed, even when an exclusion pattern matches it — the explicit entry is the more
specific statement of intent, and dropping it quietly would be a trap. Entries that match
nothing are fine, and so are duplicates: a subtraction set is allowed to name repositories that
do not exist yet. But a pattern that ends up reaching nothing at all is an error, not a silent
contribution of nothing::

   error: every repository matching 'yourorg/*' is excluded by exclude_repositories

Settle on a configuration you are happy with, then ship it and watch it build:

.. code-block:: sh

   rm -rf /tmp/ghr-pypi-try
   git add ghr-pypi.yml
   git commit -m "Index the whole owner, minus the internal repositories"
   git push
   gh workflow run pages.yml
   sleep 5
   gh run watch "$(gh run list --workflow pages.yml --limit 1 \
     --json databaseId --jq '.[0].databaseId')"
   gh run view --log "$(gh run list --workflow pages.yml --limit 1 \
     --json databaseId --jq '.[0].databaseId')" | grep -E 'expanded|wrote index'

Compare that ``expanded`` count with the one your laptop printed. Two numbers, one config file,
two tokens.

Step 6 — Install from it
========================

Open the index and pick a project from the landing page:

.. code-block:: sh

   echo "https://$OWNER.github.io/$NAME/"

It lists every project the whole set of repositories adds up to, under the ``title`` you set. The
first deployment of a brand-new Pages site can take a minute or two to become reachable; if you
get a 404, wait and reload.

.. code-block:: sh

   export PKG=<a project name from that page>
   python3 -m venv /tmp/ghr-pypi-check
   /tmp/ghr-pypi-check/bin/pip install --no-deps \
     --index-url "https://$OWNER.github.io/$NAME/simple/" \
     "$PKG"

``pip`` reports ``Successfully installed``. Nothing in that install came from PyPI, and nothing
came from the repository the index lives in either: ``pip`` read a page served by
``$OWNER/$NAME``, followed the link to a release asset on a repository that has never heard of
it, and verified the sha256 that came from GitHub's release asset API.

``--no-deps`` is there because ``--index-url`` replaces PyPI entirely, so a package with
dependencies has nowhere to resolve them from; :ref:`howto-avoid-pypi` covers installing from
your index and PyPI together. Clean up:

.. code-block:: sh

   rm -rf /tmp/ghr-pypi-check

What you built
==============

An index repository that publishes no packages of its own. It holds a list — or a pattern, minus
its exceptions — and a workflow that turns whatever those repositories have released into one
:pep:`503` and :pep:`691` index on GitHub Pages. The packages never moved; they are still release
assets on the repositories that made them, and the index is a directory of static files with no
server, no database and no copies of anything.

Adding a repository to the index is now an edit to one file, or, if it matches the pattern
already there, nothing at all.

Where to go next
================

* :ref:`howto-rebuild-on-release` — **start here.** This index has a staleness problem the
  previous model did not: it rebuilds on ``workflow_dispatch`` and on nothing else, because a
  release in ``yourorg/lib-one`` delivers its event to ``yourorg/lib-one``. That guide is the
  three ways to close the gap, and which one you can use is decided by your permissions rather
  than your preference.
* :ref:`howto-index-an-organization` — patterns and ``exclude_repositories`` in full, and what a
  pattern costs against a large owner.
* :ref:`howto-aggregate-repositories` — the fixed-list form, and how filename collisions resolve.
* :ref:`howto-private-repository` — the answer for repositories that are not public.
* :ref:`configuration` and :ref:`cli` document every key, option and error message.
