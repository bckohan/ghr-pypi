.. include:: ../refs.rst

.. _cli:

======================
Command Line Interface
======================

``ghr-pypi`` has two commands. ``index`` reads the releases of one or more GitHub
repositories, collects their wheel and sdist assets, and writes a :pep:`503` package index
into a directory of your choosing; it never starts a server and writes nothing outside
``--out`` and ``--target-out``. ``extract-meta`` writes a wheel's :pep:`658` core metadata to a
``.metadata`` file beside it, for upload as a release asset. Running ``ghr-pypi`` with no
command prints help and exits 2.

Synopsis
========

.. code-block:: text

   ghr-pypi index [REPO]... [--out DIRECTORY] [--config PATH] [--token TOKEN] [--mirror]
                  [--target NAME] [--target-out DIRECTORY]
   ghr-pypi extract-meta PATH...

The package installs the ``ghr-pypi`` console script. It can equally be run without
installing:

.. code-block:: sh

   uvx ghr-pypi index
   python -m pip install ghr-pypi && ghr-pypi index yourorg/yourrepo --out site

Reference
=========

.. typer:: ghr_pypi.cli:app
   :prog: ghr-pypi
   :width: 80
   :show-nested:

``index`` invocation forms
==========================

Repositories are named in one of two places: the ``repositories`` key of a
``--config`` file, or the positional ``REPO`` arguments. The two may not be
combined — passing both is an error, raised before the file is even read.

``GITHUB_REPOSITORY`` is the fallback for either. As a *source of repositories*
it is consulted only when neither the file nor the command line names one, so it
never conflicts with what you typed; GitHub Actions sets it for every step,
which is why it has to be harmless next to a config file. It still has two other
effects whenever it is set — it supplies the index URL (see
:ref:`the URL derivation rules <cli-url-derivation>`) and it is validated
eagerly. If nothing names a repository, the command exits 1.

No arguments
------------

.. code-block:: sh

   ghr-pypi index

Inside GitHub Actions this is the whole invocation: ``GITHUB_REPOSITORY`` names
the repository being built and ``--out`` defaults to ``_site``, which is also
the directory ``actions/upload-pages-artifact`` uploads by default. Exactly one
repository is resolved this way, so the landing page comes out titled
``owner/repo package index`` and advertising ``https://owner.github.io/repo/``,
just as if you had typed the slug.

One or more repositories
------------------------

.. code-block:: sh

   ghr-pypi index yourorg/yourrepo --out site
   ghr-pypi index yourorg/lib-one yourorg/lib-two --out site

Each argument is a bare ``OWNER/NAME`` slug — not a URL, not a clone path.
Repeating one, ignoring case, is an error.

The name half may instead be an ``fnmatch`` pattern (``*``, ``?``, ``[seq]``),
which expands to every matching repository the token can read in that owner:

.. code-block:: sh

   ghr-pypi index 'yourorg/*' --out site
   ghr-pypi index yourorg/lib-one 'yourorg/tools-*' --out site

**The quotes are required.** Unquoted, ``yourorg/*`` is a shell glob before it is
an argument, and what happens next depends on the shell and the working
directory. ``zsh`` aborts with "no matches found" before ``ghr-pypi`` runs.
``bash`` passes an unmatched glob through unchanged, so it works by accident.
``bash`` with ``nullglob`` set drops the argument entirely, leaving
``error: provide REPO..., set GITHUB_REPOSITORY, or use --config`` — or, inside
Actions, a silent fall back to ``$GITHUB_REPOSITORY``. And in a
directory that happens to contain a ``yourorg/`` subdirectory, every shell
expands the glob to those local paths, which then reach the API as literal
repository names. Single quotes, double quotes or a backslash escape all settle
the question.

The owner half is never a pattern: there is no GitHub endpoint for "every
organization I can see". Matches are sorted and spliced in where the pattern
stood — so the explicit ``yourorg/lib-one`` above still wins a duplicate-filename
tie-break against anything ``yourorg/tools-*`` pulls in — under the rules given
for :ref:`config-repositories`. Each expansion prints one line on stderr::

   expanded 'yourorg/tools-*' to 3 repositories

The count is how many repositories that pattern *newly added*, not how many it
matched, so the second of two overlapping patterns can legitimately report ``0``.

Subtracting from an expansion needs a config file — ``exclude_repositories``
has no command line equivalent. See :ref:`howto-index-an-organization`.

Defaults shared by both command line forms
------------------------------------------

Whichever of the two above you use, every setting takes its default except the
title and the URL:

* The landing page title becomes ``yourorg/yourrepo package index`` when exactly
  one repository is resolved, and ``Package index`` otherwise.
* The index URL advertised on the landing page is the GitHub Pages URL of
  ``GITHUB_REPOSITORY`` whenever that is set. The repository running the build
  is the one serving the site, even when it is indexing someone else's assets.
* Failing that, it is the Pages URL of the single resolved repository. Several
  repositories given outside Actions leave no URL at all, and the landing page
  then shows no install example.

In a Pages URL the owner is lower-cased and the repository name is used as
given.

``--mirror`` and ``--target`` are the only behavioral switches available on the
command line; everything else requires a configuration file. ``--target-out`` is
not one of them — it names a directory rather than changing what is built, so it
is accepted in every form, exactly like ``--out``.

Configuration file
------------------

.. code-block:: sh

   ghr-pypi index --config index.yml --out site

Required for indexing more than one repository from a fixed list, and the only
way to set ``title``, ``url``, ``templates``, ``formats``, ``missing_digest``,
``metadata``, or ``exclude_repositories``. See :ref:`configuration` for every
key. Repositories must be listed in the file, not on the command line — passing
both is an error, though
``GITHUB_REPOSITORY`` may be set alongside a config file and is used only when
the file omits ``repositories``. ``--mirror`` and ``--target`` are both rejected
in this form — set ``assets: mirror`` and ``target: <name>`` in the file
instead, so that the file remains the whole description of the build.

``--out`` and ``--target-out`` are the exception, and are accepted alongside
``--config``: they say where this run puts its output, not what the output is.
Where the site is written is a property of the machine running the build — a
runner's workspace, a deploy directory — so pinning it inside a file that is
committed once and run everywhere would be the wrong place for it.

.. _cli-url-derivation:

A configuration file never derives ``url`` from its own ``repositories``.
Omitting ``url`` means "no install example on the landing page": a repository
listed in the file is not necessarily the one serving the site, and the tool
will not guess. The command line form does guess, because there the repository
you named is normally your own.

The one thing that overrides this is ``GITHUB_REPOSITORY``. When it is set, its
Pages URL is used in **both** forms — including alongside a config file that
omits ``url`` — because the repository running the build is the host. Set
``url`` explicitly whenever the site is served from anywhere else.

``index`` options
=================

``--out DIRECTORY``
   Where the site is written. Defaults to ``_site``, matching
   ``actions/upload-pages-artifact``. The directory and its parents are created
   if they do not exist. Existing files are overwritten; nothing is deleted, so
   stale projects or mirrored files from a previous build survive unless you
   clear the directory first.

``REPO...``
   Zero or more repositories to index, each as ``OWNER/NAME``. The name half may
   be an ``fnmatch`` pattern — ``ghr-pypi index 'yourorg/*'`` indexes every
   repository the token can read in that owner. **Quote the pattern**, so the
   shell does not try to glob it against the working directory; the owner half
   may never be a pattern. Defaults to the
   ``GITHUB_REPOSITORY`` environment variable, which is validated whenever it is
   set — even when the repositories come from elsewhere — so that a broken
   environment fails before any network request. It may not itself be a pattern.
   An empty value counts as unset. Must not be combined with ``--config``.

``--config PATH``
   Path to the YAML configuration file. Its ``repositories`` key replaces the
   positional arguments, which must then be omitted. Relative paths in the file
   (``templates``) resolve against the file's own directory, not the working
   directory.

``--token TOKEN``
   GitHub API token, used as a bearer token for the releases API and — under mirroring — for
   the authenticated asset downloads. **Always required**, even for public repositories:
   unauthenticated GitHub API requests are rate-limited far too aggressively for a build.

``--mirror``
   Download every asset into ``<out>/files/`` and link to those copies relatively instead of
   linking to GitHub. Shorthand for ``assets: mirror``. Command line form only — with
   ``--config``, set ``assets: mirror`` in the file. See :ref:`config-assets` for the full
   behavior.

``--target NAME``
   The deployment target that writes host artifacts beside the site — ``static`` (the
   default, which writes nothing), ``cloudflare``, ``nginx``, or any name an installed plugin
   registers. A target never changes the index or rewrites a URL; that is ``--mirror``'s
   axis. See :ref:`targets`.

   Like ``--mirror`` this is a behavioral switch — it changes what the run produces — so it
   is **rejected with** ``--config``; set ``target:`` in the file instead. The name is
   resolved before the first network request, so an unknown one costs nothing: the command
   exits 1 listing every registered name.

``--target-out DIRECTORY``
   Where a target writes artifacts that must **not** be published. Defaults to the working
   directory, and is created along with its parents if it does not exist.

   Unlike ``--target``, this is a path rather than a behavioral switch, so it is **allowed
   with** ``--config`` exactly as ``--out`` is. Nothing written here goes into ``--out``, and
   nothing under ``--out`` is written here: anything below ``--out`` is published to whoever
   can reach the index, which is why an nginx snippet or a deploy script belongs on this side
   of the line. Targets that only write publishable files — ``cloudflare``'s ``_headers``,
   for instance — ignore it.

   Because the default is the working directory, ``ghr-pypi index --target nginx`` drops
   ``ghr-pypi.conf`` wherever you ran it. Every path a target writes is echoed on stdout, one
   line each, so an artifact landing outside the site is visible in the log rather than a
   surprise at commit time.

``index`` and ``GITHUB_TOKEN``
==============================

``--token`` reads its default from the ``GITHUB_TOKEN`` environment variable, so the token
never has to appear in a command line or a process listing. ``extract-meta`` reads no token;
it never talks to GitHub.

Inside GitHub Actions the automatically provided ``github.token`` is sufficient for the
repository the workflow runs in:

.. code-block:: yaml

   - name: Build the package index
     env:
       GITHUB_TOKEN: ${{ github.token }}
     run: uvx ghr-pypi index

For repositories other than the one running the workflow — that is, for any aggregating
configuration — ``github.token`` is not enough. Use a fine-grained personal access token or a
GitHub App installation token with read access to each repository's contents, stored as a
secret:

.. code-block:: yaml

   env:
     GITHUB_TOKEN: ${{ secrets.INDEX_TOKEN }}

.. _cli-extract-meta:

``extract-meta``
================

.. code-block:: sh

   ghr-pypi extract-meta dist/
   ghr-pypi extract-meta dist/demo-1.0-py3-none-any.whl

Writes each wheel's :pep:`658` core metadata to ``<wheel>.metadata`` beside the
wheel, overwriting any existing file, and prints one line per wheel followed by
a count. Each ``PATH`` is either a wheel or a directory; a directory is scanned
for ``*.whl`` **without recursing**. Repeated paths are processed once. A
directory scan passes over sdists — core metadata comes from the wheel — but an
sdist named explicitly on the command line is an error, not a no-op.

Every payload is read before any file is written, so a wheel that cannot be read
fails before a single sidecar exists. The write phase is not rolled back: if a
sidecar cannot be written, the ones already written stay on disk.

This exists for release workflows. The index can only advertise metadata that
lives at the wheel's own URL plus ``.metadata``, so in link mode the sidecar has
to be uploaded as an asset of the same release as its wheel — see
:ref:`howto-publish-metadata`.

Every ``extract-meta`` exit-1 condition
---------------------------------------

Nothing is skipped with a warning — a release that silently ships no metadata is
the failure this command prevents.

``error: <path> does not exist``
   A ``PATH`` names nothing on disk. Every ``PATH`` is resolved before any wheel
   is opened, so this fires before anything is read.

``error: <path> is not a wheel``
   A ``PATH`` exists and is not a directory, but does not end in ``.whl``. An
   sdist passed explicitly lands here.

``error: no wheels in <path>``
   A ``PATH`` is a directory whose top level contains no ``*.whl``. The scan
   does not recurse, so wheels in a subdirectory do not count.

``error: cannot extract metadata from <wheel>: <reason>``
   The wheel could not be read. ``<reason>`` is ``File is not a zip file`` for
   something that is not a wheel at all, ``no unique .dist-info/METADATA
   member`` for a zip carrying zero or more than one top-level
   ``*.dist-info/METADATA``, and the operating system's message for a wheel
   found by a directory scan that cannot be opened. An unreadable path named
   explicitly is rejected earlier, with exit 2. Raised during the read phase,
   so no sidecar has been written yet.

``error: cannot write <path>: <reason>``
   A sidecar could not be written — an unwritable directory, a full disk, a
   ``.metadata`` path that is itself a directory. This is the one failure that
   can leave earlier sidecars behind; the count line is not printed.

Exit codes
==========

.. list-table::
   :header-rows: 1
   :widths: 10 90

   * - Code
     - Meaning
   * - ``0``
     - The command did its work. ``index`` prints ``wrote index for N project(s) to
       <out>``, followed by one ``wrote <path> for the <target> target`` line per artifact
       the target wrote — none at all under the default ``static``; ``extract-meta`` prints
       one ``wrote <path>`` line per wheel followed by
       ``extracted metadata from N wheel(s)``. Both write to stdout.
   * - ``1``
     - The command failed. A single line beginning with ``error:`` is printed on stderr.
       ``index`` normally writes no site at all, so nothing is deployed; two cases leave
       content under ``--out`` anyway. Under ``assets: mirror`` each verified asset lands in
       ``<out>/files/`` as it is downloaded, so a mirroring failure leaves the files fetched
       so far — reused, not re-downloaded, by the next run. And the two target failures below
       happen after the whole site is written, leaving a complete index.
       ``extract-meta`` has written no
       sidecars if the failure was a read; if a write failed, the sidecars written before
       it remain.
   * - ``2``
     - Command line usage error, raised by the argument parser before any work starts — an
       unknown or missing command, a missing required argument, an unknown option, a
       missing option value, an unparseable value. Bare ``ghr-pypi`` and ``extract-meta``
       with no ``PATH`` both land here. Usage text is printed.

Every ``index`` exit-1 condition
--------------------------------

The checks below run in this order; the first one that fails ends the run.
``extract-meta``'s failures are listed under :ref:`cli-extract-meta` above.

``error: provide --token or set GITHUB_TOKEN``
   No token was supplied, or the supplied value was empty. Checked before anything else is
   validated, so this masks other problems until it is fixed.

``error: GITHUB_REPOSITORY '...' is not OWNER/NAME``
   The environment variable is set but is not exactly two non-empty
   ``/``-separated parts. It is validated whenever it is set — even when the
   repositories come from arguments or a config file — so that a broken
   environment fails before any network request. An empty value counts as unset
   and is not an error.

``error: GITHUB_REPOSITORY '...' may not be a pattern``
   The environment variable contains ``*``, ``?`` or ``[``. It names the one
   repository the build is running in, so there is nothing for a pattern to
   mean there — and a value arriving from the environment is exactly where a
   silently expanded glob would be hardest to notice. Patterns belong in a
   positional argument or in the config file's ``repositories``. Checked
   whenever the variable is set, even when the repositories come from
   elsewhere.

``error: with --config, list repositories in the config file``
   Positional ``REPO`` arguments were combined with ``--config``. Setting
   ``GITHUB_REPOSITORY`` does not trigger this.

``error: with --config, set 'assets: mirror' in the config file``
   ``--mirror`` was combined with ``--config``.

``error: with --config, set 'target' in the config file``
   ``--target`` was combined with ``--config``. ``--target-out`` is not affected — it is a
   path, and is accepted in both forms.

``error: <config validation message>``
   The configuration file could not be read, parsed, or validated. Each message is listed
   verbatim with its cause and fix in :ref:`configuration`.

``error: <path> has no 'repositories' and GITHUB_REPOSITORY is not set``
   The config file omits ``repositories`` (or sets it to nothing) and there is
   no environment variable to fall back to.

``error: repository '...' is not OWNER/NAME``
   A positional ``REPO`` is not exactly two non-empty ``/``-separated parts. Passing a URL
   such as ``https://github.com/yourorg/repo`` fails here.

``error: repository '...' may not use a pattern in the owner``
   A positional ``REPO`` puts ``*``, ``?`` or ``[`` in the half before the ``/``.
   Only the name half may be a pattern. The config file raises the same message
   prefixed with its path.

``error: repository '...' given more than once``
   The same repository was passed twice on the command line; the comparison
   ignores case. There is no equivalent check across sources — an argument that
   differs only in case from ``GITHUB_REPOSITORY`` is taken as given.

``error: provide REPO..., set GITHUB_REPOSITORY, or use --config``
   No repositories were resolved from any source.

``error: unknown target '...'; available: ...``
   ``--target``, or the config file's ``target``, names something that is not registered.
   Every registered name is listed, sorted — built-ins and plugin targets alike, so the
   message doubles as the answer to "what can I pick?". Raised before the first network
   request, because the answer is knowable from the arguments alone and discovering it after
   a full build would be a wasted run. The name is deliberately *not* checked while the
   config file is validated; see :ref:`config-target`.

``error: cannot create --target-out <path>: <reason>``
   ``--target-out`` and its parents could not be created — a path component that is a file, a
   read-only filesystem, permissions. Checked at the same point, and for the same reason, as
   the target name: before anything is downloaded.

``error: '...' is not a visible organization or user``
   The owner half of a pattern is not an account this token can see. GitHub
   answers 404 for accounts a token cannot reach just as it does for
   repositories, so a typo and a permissions gap look the same. Only patterns
   reach this check — an explicit ``OWNER/NAME`` is never looked up as an
   account.

``error: no repositories matched '...'``
   A pattern expanded to nothing. Either the owner has no matching repository,
   or the token cannot see the ones it has — on a personal account
   ``/users/{owner}/repos`` lists **public** repositories only, so a private one
   is invisible to any pattern; see :ref:`howto-org-user-accounts`.

``error: every repository matching '...' is excluded by exclude_repositories``
   The pattern matched, and :ref:`config-exclude-repositories` then removed
   every match, which leaves the pattern saying nothing. Distinct from the
   message above on purpose: the fix is to narrow one of the two keys, not to
   go looking for missing repositories.

``error: listing repositories failed: <reason>``
   Expanding a pattern needs a repository listing and that request failed — a
   bad or under-scoped token, rate limiting, a network failure, or an owner with
   more than 10,000 repositories, which the pager refuses to walk rather than
   silently truncating.

``error: GitHub API request for <repo> failed: <reason>``
   The releases API call failed — bad or expired token, insufficient permissions, a
   nonexistent or inaccessible repository, rate limiting, or a network failure. The named
   repository is the one being fetched when the failure occurred. Releases are read in full,
   100 per request; a repository with more than **10,000** of them ends here too, with
   ``... more than 10000 items; refusing to page further``. That cap exists so a server that
   never returns a short page cannot spin forever, and it fails rather than truncating,
   because a quietly short index is worse than a failed build.

``error: downloading a release asset failed: <reason>``
   An asset download failed. This happens while hashing digest-less assets under
   ``missing_digest: download``, and again while mirroring.

``error: no package assets found in releases of <repos>; refusing to build an empty index``
   The repositories were read successfully but contained no wheel or sdist assets in any
   non-draft release. The index is deliberately not written, so a misconfigured run can never
   replace a working index with an empty one. Common causes: assets attached only to draft
   releases, a token that cannot see the releases, a repository that publishes to PyPI but
   attaches nothing to its releases, or ``missing_digest: omit`` removing everything.

``error: <mirroring message>``
   A mirrored download failed verification. The messages are ``<file>: unsafe path``,
   ``<file>: asset has no API download URL``, ``<file>: refusing to fetch non-https URL:
   ...``, ``<file>: truncated download (N of M bytes)``, and ``<file>: downloaded sha256 ...
   does not match advertised digest ...``. The partially written file is removed and any
   previously mirrored copy is left intact.

``error: <target> target failed: <reason>``
   The target raised an ``OSError`` while writing — an unwritable directory, a full disk, a
   path that is already a directory. Targets run **after** the site is on disk, against the
   finished tree, so ``--out`` holds a complete, usable index even though the command exits
   1; only the host artifacts are missing or half-written. A target that ignored the
   ``Sequence`` return type and yielded its paths as it wrote them also fails here, cleanly:
   the return value is materialized inside this guard rather than iterated afterwards.

``error: <target> target returned <type>, expected a sequence of paths``
   A target's ``emit`` returned something that is not a sequence of paths — most often
   ``None``, from a plugin that forgot to return the paths it wrote. Reported here rather
   than allowed to surface as a ``TypeError`` while the paths are echoed, which would put a
   traceback *after* the success line. Like the failure above it happens after the site is
   written, so the index is complete. See :ref:`targets` for the contract.

``index`` warnings
------------------

Diagnostics that do **not** fail the build are printed on stderr and start with ``warning:``:
duplicate filenames across repositories, assets skipped for unsafe names, assets omitted
under ``missing_digest: omit``, wheels whose core metadata could not be extracted, and the
per-repository :pep:`658` metadata coverage report. They are worth reading in CI logs — an
index can be published successfully and still be missing what you expected.

Pattern expansion also reports on stderr, without the ``warning:`` prefix, because it is
progress rather than a diagnostic — one ``expanded '<pattern>' to N repositories`` line per
pattern, as described above.

``extract-meta`` emits no warnings at all; every problem it detects is fatal.

Examples
========

Index one repository for GitHub Pages
-------------------------------------

.. code-block:: sh

   export GITHUB_TOKEN=ghp_...
   ghr-pypi index yourorg/yourrepo --out site
   # wrote index for 2 project(s) to site

Serve ``site/`` at ``https://yourorg.github.io/yourrepo/`` and install from it:

.. code-block:: sh

   pip install --index-url https://yourorg.github.io/yourrepo/simple/ yourpackage

Aggregate several repositories
------------------------------

.. code-block:: yaml

   # index.yml
   repositories:
     - yourorg/lib-one
     - yourorg/lib-two
   title: yourorg package index
   url: https://yourorg.github.io/pypi/

.. code-block:: sh

   GITHUB_TOKEN="$INDEX_TOKEN" ghr-pypi index --config index.yml --out site

The token must be able to read every listed repository; a workflow's built-in
``github.token`` cannot.

Index a private repository
--------------------------

.. code-block:: sh

   ghr-pypi index yourorg/private-repo --out site --token "$GITHUB_TOKEN" --mirror

``--mirror`` downloads each asset through GitHub's authenticated asset API into
``site/files/`` and links to those copies, because direct release-asset links to a private
repository are not fetchable by pip. Serve the resulting directory behind whatever
authentication your host provides; pip and uv both understand basic auth and ``netrc``:

.. code-block:: sh

   pip install --index-url https://user:pass@packages.example/simple/ yourpackage

Emit the JSON API only
----------------------

.. code-block:: yaml

   # json-only.yml
   repositories:
     - yourorg/lib-one
   formats: [json]

.. code-block:: sh

   ghr-pypi index --config json-only.yml --out site

Writes ``site/simple/index.json`` and ``site/simple/<project>/index.json`` and nothing else —
no landing page, no HTML. Useful when a webserver in front of the files performs
``Accept``-header content negotiation, or when the index is consumed only by tools that speak
:pep:`691`.

Annotate a release with :pep:`658` metadata
-------------------------------------------

.. code-block:: sh

   ghr-pypi extract-meta dist/
   # wrote dist/yourpackage-1.0-py3-none-any.whl.metadata
   # extracted metadata from 1 wheel(s)
   gh release upload "$GITHUB_REF_NAME" dist/*.whl.metadata

The sidecar has to reach the same release as its wheel; see
:ref:`howto-publish-metadata` for the workflow this belongs in.
