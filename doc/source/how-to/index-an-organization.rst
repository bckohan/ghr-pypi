.. include:: ../refs.rst

.. _howto-index-an-organization:

====================================
How do I index a whole organization?
====================================

Put an ``fnmatch`` pattern in the **name** half of a ``repositories`` entry. ``yourorg/*``
stands for every repository in ``yourorg`` that the token can read, and the build indexes the
releases of all of them:

.. code-block:: yaml

   # index.yml
   repositories:
     - yourorg/*
   title: yourorg package index
   url: https://yourorg.github.io/pypi/

.. code-block:: sh

   ghr-pypi index --config index.yml --out site

The same thing works as a positional argument, with no config file at all:

.. code-block:: sh

   ghr-pypi index 'yourorg/*' --out site

**Quote the pattern.** Unquoted, the shell tries to glob ``yourorg/*`` against your working
directory first — in ``bash`` it usually passes through unchanged and works by accident, in
``zsh`` it fails outright with "no matches found", and in a directory that happens to contain
a ``yourorg/`` folder it silently expands to local paths. Single quotes, double quotes or a
backslash escape all do the job. Inside a GitHub Actions ``run:`` step the same rule applies:
the step body is a shell script.

``*``, ``?`` and ``[seq]`` all work, so ``yourorg/lib-*`` takes a family and
``yourorg/lib-[12]`` takes exactly two. Matching ignores case. The **owner** half may never be
a pattern — there is no GitHub endpoint for "every organization I can see", so ``*/lib`` is
rejected before any request is made.

What a pattern includes
=======================

Everything the token can read in that owner — **forks and archived repositories included**.
There is no hidden filter, deliberately: a fork can be the repository that publishes your
wheels, and "archived" says nothing about whether its past releases should still be
installable. What you get is exactly what the token sees.

``exclude_repositories`` is the escape hatch:

.. code-block:: yaml

   repositories:
     - yourorg/*
   exclude_repositories:
     - yourorg/*-internal
     - yourorg/abandoned-fork

It subtracts from **expansions only**. A repository named explicitly in ``repositories`` is
always indexed, even if an exclusion pattern matches it — the explicit entry is the more
specific statement, and dropping it silently would be a trap. Entries that match nothing are
fine, and so are duplicates; see :ref:`config-exclude-repositories` for why.

Two failures are worth telling apart. ``error: no repositories matched 'yourorg/lib-*'`` means
the pattern found nothing, while ``error: every repository matching 'yourorg/lib-*' is
excluded by exclude_repositories`` means it found things and the exclusions took all of them.
Both exit 1 rather than contributing nothing quietly.

Order still matters
===================

Duplicate filenames across repositories resolve first-occurrence-wins, and a pattern's matches
land where the pattern stood. Listing a repository *above* a pattern is therefore how you make
its copy of a shared filename the winning one:

.. code-block:: yaml

   repositories:
     - yourorg/canonical-lib   # this copy of any shared filename wins
     - yourorg/*               # ... and everything else follows it

:ref:`config-repositories` gives the exact ordering rules. While you are tuning the order,
read the stderr report — one line per pattern, counting what that pattern *newly added* rather
than what it matched::

   expanded 'yourorg/canonical-*' to 2 repositories
   expanded 'yourorg/*' to 9 repositories

.. _howto-org-user-accounts:

Personal accounts get public repositories only
==============================================

This is the limitation to know before you rely on a pattern.

A pattern is expanded by listing the owner's repositories: ``/orgs/{owner}/repos`` first,
falling back to ``/users/{owner}/repos`` when GitHub answers 404 because the owner is a person
rather than an organization. **That user endpoint returns public repositories only.** GitHub
has no endpoint that lists another account's private repositories, at any permission level.

So ``someuser/*`` finds their public repositories and nothing else. Private repositories on a
personal account must be listed explicitly:

.. code-block:: yaml

   repositories:
     - someuser/*                  # the public ones
     - someuser/private-tool       # named, because no listing would find it

There is an endpoint that would list *your own* private repositories — ``/user/repos``, "the
repositories of whoever this token belongs to" — and it is deliberately not used. It answers
for exactly one kind of credential: a personal access token belonging to a human. A workflow's
``github.token`` is not a user, and neither is a GitHub App installation token; both get
nothing useful from it. Wiring a pattern to an endpoint that works only when the build runs
under someone's personal token would make ``yourorg/*`` mean different things in CI and on a
laptop, which is worse than not supporting it.

Organizations do not have this problem: ``/orgs/{owner}/repos`` returns every repository the
token is authorized for, private ones included, so a fine-grained token or App installation
with **Contents: Read-only** on the organization sees them all.

What it costs
=============

Roughly:

* one API call to list the owner's repositories, plus one more per 100 repositories they
  have — and one extra on a personal account, where ``/orgs/`` has to 404 before
  ``/users/`` is tried;
* one API call per repository to read its releases, plus one more per 100 releases it has;
* no package bytes at all, unless an asset predates GitHub's asset digests (see
  :ref:`config-missing-digest`) or you are mirroring.

One listing is fetched per owner and cached case-insensitively, so ``Yourorg/lib-*`` and
``yourorg/app-*`` in the same file cost one listing between them, not two.

The practical consequence is that a pattern's cost scales with the *whole* owner, not with the
repositories that actually publish packages. An organization of 200 repositories where five
ship wheels still costs about 200 release requests per build. If that becomes the slow part,
narrow the pattern (``yourorg/lib-*``) or list the five repositories.

Give it a token that can read them all
======================================

A workflow's built-in ``github.token`` is scoped to the repository the workflow runs in — but
against a **public** organization ``/orgs/{owner}/repos`` still answers it with that
organization's public repositories, so the pattern expands. It expands to a *silently partial*
set: every private repository is missing, and the build succeeds anyway. That is the failure
mode to watch for, because nothing in the log says the index is short.

Use a fine-grained personal access token or a GitHub App installation token with
**Contents: Read-only** on the organization, stored as a secret:

.. code-block:: yaml

   - name: Build the package index
     env:
       GITHUB_TOKEN: ${{ secrets.INDEX_TOKEN }}
     run: uvx ghr-pypi index --config index.yml --out site

A token that cannot see the owner at all fails with
``error: 'yourorg' is not a visible organization or user`` — GitHub answers 404 for accounts a
token cannot reach exactly as it does for repositories, so a typo and a missing grant look
identical.

One last place a pattern is refused: ``$GITHUB_REPOSITORY``. It names the single repository
the build is running in, and a pattern there is rejected with
``error: GITHUB_REPOSITORY 'yourorg/*' may not be a pattern``.

Next
====

* :ref:`configuration` — :ref:`config-repositories` and
  :ref:`config-exclude-repositories` in full, with every validation message.
* :ref:`cli` — the quoting rule, the ``expanded ...`` report, and every exit-1 condition.
* :ref:`howto-aggregate-repositories` — the fixed-list form, and how filename collisions
  resolve.
* :ref:`howto-rebuild-on-release` — nothing in ``yourorg`` rebuilds this index by releasing;
  one organization webhook covers every repository a pattern reaches, including new ones.
