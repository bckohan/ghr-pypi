.. include:: ../refs.rst

.. _howto-build-failed:

=================================================
Why is my index empty, or why did the build fail?
=================================================

Every failure prints one line beginning with ``error:`` and exits 1 — read that line first.
Reproduce it locally in seconds, where the warnings are easier to see than in a CI log:

.. code-block:: sh

   GHR_PYPI_TOKEN=$(gh auth token) uvx ghr-pypi index yourorg/yourrepo --out /tmp/site

"No package assets found"
=========================

::

   error: no package assets found in releases of yourorg/yourrepo; refusing to build an
   empty index

The releases were read successfully and contained nothing indexable. In order of likelihood:

* **The assets are not wheels or sdists.** Only ``.whl`` and ``.tar.gz`` files are collected;
  a ``.zip`` sdist, a ``.sigstore`` bundle, a checksum file or a bare ``.metadata`` sidecar is
  ignored, silently. (The reverse gotcha: *any* ``.tar.gz`` is indexed, under whatever name
  precedes its final ``-``, so an unrelated tarball can appear as a project.)
* **The releases are drafts.** Draft releases are skipped entirely.
* **The project publishes to PyPI but attaches nothing to its releases.** Building a wheel in
  CI is not the same as uploading it to the release; check with
  ``gh release view <tag> --json assets --jq '.assets[].name'``.
* **``missing_digest: omit`` removed everything.** Each dropped file warns
  ``... has no digest, omitted (missing_digest=omit)``. Switch to ``download`` or
  ``no-fragment``.
* **Nothing was ever released.** Tags are not releases — a pushed tag publishes nothing to
  the releases API. Check with ``gh release list``. A ``yourorg/*`` pattern makes this easy
  to hit, because it pulls in every repository the token can read whether or not it
  publishes packages; the index is empty only when *none* of them do.

The other error lines
=====================

``error: provide --token or set GHR_PYPI_TOKEN``
   Checked before anything else, so it masks other problems until you fix it. A token is
   required even for public repositories.

``error: GitHub API request for <repo> failed: ...``
   Bad, expired or under-scoped token; a repository that does not exist as far as this token
   is concerned (GitHub answers 404 rather than 403 for repositories a token cannot see);
   rate limiting; a network failure.

``error: provide REPO..., set GITHUB_REPOSITORY, or use --config``
   No repositories were resolved. Pass one or more ``OWNER/NAME`` arguments,
   run inside GitHub Actions (which sets ``GITHUB_REPOSITORY``), or supply a
   config file that lists them.

``error: with --config, list repositories in the config file``
   Positional arguments and ``--config`` are mutually exclusive. Move the
   repositories into the file's ``repositories`` key. ``GITHUB_REPOSITORY``
   does not trigger this — it is used only when the file omits the key.

``error: <path> has no 'repositories' and GITHUB_REPOSITORY is not set``
   The config file omits ``repositories`` and there is nothing to fall back to.
   Add the key, or pass the repository in the environment.

``error: GITHUB_REPOSITORY 'x' is not OWNER/NAME``
   The environment variable is malformed. It is validated whenever it is set,
   even when the repositories come from elsewhere, so that a broken environment
   fails before any network request.

``error: repository 'x' given more than once``
   The same repository was passed twice on the command line (the comparison
   ignores case). There is no such check across sources — an argument that
   differs only in case from ``GITHUB_REPOSITORY`` is taken as given.

``error: no repositories matched 'yourorg/lib-*'``
   A pattern expanded to nothing: no repository in that owner matches, or the token cannot
   see the ones that do. A personal account is the usual surprise — only its public
   repositories are listed at all, so a private one has to be named in full. See
   :ref:`howto-org-user-accounts`.

``error: every repository matching 'yourorg/lib-*' is excluded by exclude_repositories``
   The pattern matched, and ``exclude_repositories`` then removed every match. Narrow one
   or the other; a repository named explicitly is never removed this way.

``error: 'yourorg' is not a visible organization or user``
   The owner half of a pattern is not an account this token can see (GitHub answers 404 for
   accounts a token cannot see, exactly as it does for repositories). Only patterns can
   raise this — an explicit ``OWNER/NAME`` fails later, when its releases are fetched.

``error: GITHUB_REPOSITORY 'yourorg/*' may not be a pattern``
   The environment variable names one repository, never a set of them. Put the pattern in
   the config file's ``repositories`` or in a positional argument.

``error: listing repositories failed: ...``
   Expanding a pattern needs a repository listing, and that request failed — bad or
   under-scoped token, rate limiting, or a network failure.

``error: with --config, set 'assets: mirror' in the config file``
   ``--mirror`` is a flag of the command line form only; with a config file the setting is
   the ``assets`` key. Write ``assets: mirror`` in the file. (The old boolean ``mirror: true``
   still works and warns — see :ref:`config-assets`.)

``error: with --config, set 'target' in the config file``
   Same rule for ``--target``: it changes what the build produces, so with a config file it
   is the ``target`` key. ``--target-out`` is *not* affected — it is only a path, like
   ``--out``, and is accepted with both forms.

``error: <config message>``
   Configuration validation. Every message is listed with its cause and fix in
   :ref:`configuration`.

``error: unknown target 'x'; available: cloudflare, nginx, static``
   ``--target``, or the config file's ``target``, names something that is not registered.
   Every registered name is listed, so the message is also the answer. A plugin target
   missing from that list usually means its distribution is not installed in the environment
   running the build, or was installed after its entry point was added — re-install it. The
   check runs before the first network request, so nothing has been fetched.

``error: cannot create --target-out <path>: ...``
   ``--target-out`` and its parents could not be created — a path component that is a file, a
   read-only filesystem, permissions. Also checked before anything is downloaded.

``error: downloading a release asset failed: ...``
   A file transfer failed — while hashing digest-less assets, or while mirroring.

``error: <target> target failed: ...``
   The deployment target could not write its artifacts — an unwritable ``--target-out``, a
   full disk. **The index itself is complete**: targets run after the whole site is written,
   so ``--out`` holds a usable index despite the exit 1. Re-running only the build is not
   necessary; fix the destination and re-run.

``error: <target> target returned NoneType, expected a sequence of paths``
   A plugin target's ``emit`` did not return the paths it wrote — forgetting the ``return``
   is the usual cause. Only third-party targets can raise this; the built-ins cannot. As
   above, the index is already written and complete. See :ref:`howto-write-a-target`.

Exit status 2, with usage text, means the command line itself did not parse (an unknown
option, a missing option value).

Warnings are not failures
=========================

Lines beginning with ``warning:`` never stop the build: duplicate filenames, unsafe asset
names, omitted assets, unreadable wheels, and the :pep:`658` coverage report. An index can be
published successfully and still be missing what you expected, so read them.

Next
====

* :ref:`cli` — every exit-1 condition in the order it is checked.
* :ref:`configuration` — every ``ConfigError`` message, verbatim.
