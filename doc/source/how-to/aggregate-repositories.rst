.. include:: ../refs.rst

.. _howto-aggregate-repositories:

=======================================================
How do I aggregate several repositories into one index?
=======================================================

List them in a YAML config file and pass it with ``--config``. The config file replaces the
positional ``REPO`` arguments — passing both is an error — and it is the only way to keep the
list in version control alongside the settings an aggregate index needs: ``title``, ``url``,
``templates``, ``formats``, ``missing_digest``, ``metadata``, ``yanked`` and ``exclude``.

For a one-off build there is a quicker route: the positional argument takes as many
repositories as you like, so ``ghr-pypi index yourorg/lib-one yourorg/lib-two --out site``
aggregates them with no file at all. What you give up is everything above — the title falls back to
``Package index``, and the landing page gets an install example only when
``$GITHUB_REPOSITORY`` is set. Reach for the config file as soon as the index is something you
publish rather than something you inspect.

.. code-block:: yaml

   # index.yml
   repositories:
     - yourorg/lib-one
     - yourorg/lib-two
     - otherorg/vendored-tool
   title: yourorg package index
   url: https://yourorg.github.io/pypi/

.. code-block:: sh

   ghr-pypi index --config index.yml --out site

An entry's name half may also be an ``fnmatch`` pattern, so ``yourorg/*`` stands for every
repository in that owner the token can read and ``yourorg/lib-*`` for a subset of them —
see :ref:`howto-index-an-organization`.

Every wheel and sdist attached to every non-draft release of every listed repository ends up
in one flat index, keyed by :pep:`503`-normalized project name. Two repositories publishing
different projects get one project page each; two publishing the *same* project name share a
page, with the files merged.

Give it a token that can read them all
======================================

A workflow's built-in ``github.token`` can only read the repository the workflow runs in, so
it cannot build an aggregate index. Use a fine-grained personal access token or a GitHub App
installation token with **Contents: Read-only** on each repository, stored as a secret:

.. code-block:: yaml

   - name: Build the package index
     env:
       GITHUB_TOKEN: ${{ secrets.INDEX_TOKEN }}
     run: uvx ghr-pypi index --config index.yml --out site

Every release of every listed repository is read — the GitHub API is paged until it runs
out — so the cost is roughly one API call per repository plus one more per 100 releases in
any of them, and no package bytes are transferred at all unless an asset predates GitHub's
asset digests or you are mirroring. A full rebuild is therefore cheap enough to run on every
release, which is why
the config file is usually checked into a small dedicated "index" repository whose Pages site
(or CDN project) serves the result. That workflow does need a checkout — the config file has
to be on disk.

Collisions and duplicates
=========================

* Repositories are processed **in the listed order**. If two of them publish the same
  *filename*, the first occurrence wins and the loser is reported::

     warning: duplicate asset demo_lib-1.0-py3-none-any.whl ignored (https://...)

* Listing the same repository twice is rejected outright, case-insensitively:
  ``yourorg/Lib`` and ``yourorg/lib`` collide with
  ``'repositories' contains duplicates``. The command line form rejects the same thing with
  ``repository '...' given more than once``.

* Every entry must be a bare ``OWNER/NAME`` slug — not a URL, not a clone path.

Next
====

* :ref:`howto-index-an-organization` — patterns, ``exclude_repositories``, and what a
  pattern can and cannot reach on a personal account.
* :ref:`howto-rebuild-on-release` — a release in a listed repository does not rebuild this
  index on its own; the three ways to make it.
* :ref:`config-repositories` — the exact constraints and error messages.
* :ref:`cli` — why ``--mirror`` is refused with ``--config``, and what the command line form
  defaults differently.
