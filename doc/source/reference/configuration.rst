.. include:: ../refs.rst

.. _configuration:

=============
Configuration
=============

``ghr-pypi`` can be driven from the command line alone — repositories as
positional arguments, or none at all, falling back to the
``GITHUB_REPOSITORY`` environment variable — or with a YAML configuration file
given with ``--config``. Repositories may not be given both ways at once (see
:ref:`cli`).

The configuration file is the only way to aggregate a fixed list of
repositories into one index, and the only way to set ``title``, ``url``,
``templates``, ``formats``, ``missing_digest``, ``metadata``,
``missing_metadata``, ``assets``,
``target``, ``yanked``, ``exclude``, or ``exclude_repositories``. The command
line form supports ``--mirror`` and ``--target`` and otherwise uses the defaults
listed below, except that
``title`` becomes
``"<OWNER/NAME> package index"`` when exactly one repository is resolved, and
``url`` is derived from a GitHub Pages URL (``https://<owner>.github.io/<name>/``)
when one can be determined — see
:ref:`which repository that names, and when there is none <cli-url-derivation>`.

The file is read as YAML and its top level must be a mapping. Only the thirteen keys documented
here — plus the deprecated ``mirror``, described under :ref:`config-assets` — are accepted;
any other key aborts the build. Every value is validated before any network request is made,
so a configuration mistake fails immediately and cheaply.

.. code-block:: sh

   ghr-pypi index --config index.yml --out site

Summary
=======

.. list-table::
   :header-rows: 1
   :widths: 18 24 20 38

   * - Key
     - Type
     - Default
     - Notes
   * - :ref:`config-repositories`
     - list of ``OWNER/NAME`` or ``OWNER/PATTERN``
     - *optional*
     - When present: non-empty, no case-insensitive duplicates
   * - :ref:`config-exclude-repositories`
     - list of ``OWNER/NAME`` or ``OWNER/PATTERN``
     - ``[]``
     - Subtracts from pattern expansions only; duplicates allowed
   * - :ref:`config-templates`
     - string (path)
     - none
     - Resolved relative to the config file; must exist
   * - :ref:`config-title`
     - string
     - ``Package index``
     - Landing page heading and ``<title>``
   * - :ref:`config-url`
     - string (``https://``)
     - none
     - Enables the install example on the landing page
   * - :ref:`config-missing-digest`
     - ``download`` | ``no-fragment`` | ``omit``
     - ``download``
     - Rejected when ``assets`` is ``mirror``
   * - :ref:`config-formats`
     - list of ``html`` | ``json``
     - ``[html, json]``
     - Non-empty, no duplicates
   * - :ref:`config-assets`
     - ``link`` | ``mirror`` | ``redirect``
     - ``link``
     - ``mirror`` downloads assets into ``<out>/files/``; ``redirect`` needs a
       target that serves them
   * - :ref:`config-target`
     - string (a registered target name)
     - ``static``
     - Host artifacts written beside the site; plugins add names
   * - :ref:`config-metadata`
     - boolean
     - ``true``
     - :pep:`658` core metadata handling
   * - :ref:`config-missing-metadata`
     - ``extract`` | ``warn``
     - ``extract``
     - Only under ``assets: redirect``; rejected anywhere else
   * - :ref:`config-yanked`
     - project → version → reason
     - ``{}``
     - :pep:`592` yanks; files stay in the index
   * - :ref:`config-exclude`
     - project → list of versions
     - ``{}``
     - Files never enter the index at all

Keys
====

.. _config-repositories:

``repositories``
----------------

:Type: list of strings
:Default: none — falls back to ``$GITHUB_REPOSITORY``. Positional ``REPO``
          arguments are *not* a fallback: they may not be passed alongside
          ``--config`` at all.
:Constraints: When present, must be a non-empty list. Every entry must be a
              string of exactly two non-empty, ``/``-separated parts
              (``OWNER/NAME``). The name half may be an ``fnmatch`` pattern;
              the owner half may not. Entries must be unique when compared
              case-insensitively.

Omitting the key is useful when the file exists only to set ``title``,
``templates`` or ``yanked`` for the repository the build is running in: inside
GitHub Actions ``GITHUB_REPOSITORY`` supplies the repository. Writing the key
with no value (``repositories:``) is the same as omitting it. If nothing
supplies a repository, the build fails rather than producing an empty index.

The repositories whose releases are indexed. Every wheel (``.whl``) and sdist (``.tar.gz``)
attached to any non-draft release of any listed repository becomes an entry in the index.
Draft releases are skipped because their assets are not publicly downloadable.

Repositories are processed in the order given. When two repositories publish the same
filename, the first occurrence wins and the duplicate is reported on stderr::

   warning: duplicate asset demo_lib-1.0-py3-none-any.whl ignored (https://...)

.. code-block:: yaml

   repositories:
     - yourorg/lib-one
     - yourorg/lib-two

An entry's **name** half may be an ``fnmatch`` pattern — ``*``, ``?``, or a ``[seq]``
character class. The **owner** half may not, because there is no GitHub endpoint for "every
organization I can see"; ``*/lib`` is rejected before any request is made. A pattern is
expanded by listing that owner's repositories (``/orgs/{owner}/repos``, falling back to
``/users/{owner}/repos``) and matching casefolded, so ``yourorg/LIB-*`` matches a repository
named ``lib-one``. The listing is fetched once per owner and cached case-insensitively, so
``Yourorg/lib-*`` and ``yourorg/app-*`` in one file page that owner once between them rather
than twice.

Every repository the token can read is a candidate — **forks and archived repositories
included**. There is no hidden filter; :ref:`config-exclude-repositories` is the escape hatch.
What the token can read is not always what the owner has: see
:ref:`howto-org-user-accounts` before relying on a pattern against a personal account.

Matches are sorted and spliced in **where the pattern stood**, which makes order behavior
rather than presentation: an explicit entry written above a pattern still wins the
duplicate-filename tie-break against everything that pattern brings in. Overlapping patterns
de-duplicate with the first occurrence winning, and every expansion reports on stderr how many
repositories it *newly added* — so the second of two overlapping patterns can legitimately
report ``0``::

   expanded 'yourorg/lib-*' to 3 repositories

A pattern that matches nothing is an error, not a silent contribution of nothing; so is a
pattern whose every match is removed by :ref:`config-exclude-repositories`. Both messages are
listed in :ref:`cli`, because they are raised while listing repositories rather than while
validating this file.

.. code-block:: yaml

   repositories:
     - yourorg/lib-one     # explicit: stays ahead of everything below it
     - yourorg/tools-*     # every repository whose name starts with tools-
     - yourorg/*           # ... and the rest of the owner, minus the above

.. _config-exclude-repositories:

``exclude_repositories``
------------------------

:Type: list of strings
:Default: ``[]`` — nothing is subtracted
:Constraints: Must be a list when present; an empty list is fine. Every entry
              must be ``OWNER/NAME`` or ``OWNER/PATTERN`` under the same rule as
              :ref:`config-repositories` — the name half may be an ``fnmatch``
              pattern, the owner half may not. Duplicates are **accepted**.

Subtracts from what a pattern in :ref:`config-repositories` expands to. Each entry is matched
against the full casefolded ``owner/name`` slug, so it can name a single repository
(``yourorg/legacy-tool``), a family of them (``yourorg/*-internal``), or reach into another
owner entirely.

It applies to **expansions only**. A repository named explicitly in ``repositories`` is always
indexed, even when an exclusion pattern would match it: the explicit entry is the more specific
statement of intent, and silently dropping it would be a trap rather than a convenience.

An entry that matches nothing is deliberately not an error. A subtraction set is allowed to
name repositories that do not exist yet, or that this build's patterns never reach; requiring
each one to bite would make the key fragile for no gain. Duplicate entries are accepted for the
same reason, even though ``repositories`` rejects them — removing a repository twice removes it
once.

.. code-block:: yaml

   repositories:
     - yourorg/*
   exclude_repositories:
     - yourorg/*-internal
     - yourorg/legacy-tool

.. _config-templates:

``templates``
-------------

:Type: string (directory path)
:Default: none — the built-in templates are used
:Constraints: Must be a string. Resolved relative to the **directory containing the config
              file**, then required to be an existing directory.

A directory of Jinja templates that override the built-ins. A file named ``landing.html``,
``project.html``, or ``simple_root.html`` in that directory replaces the corresponding
built-in wholesale. The built-ins remain reachable under a ``builtin/`` prefix, so an override
can extend rather than replace them:

.. code-block:: html

   {% extends "builtin/landing.html" %}
   {% block footer %}<footer>&copy; yourorg</footer>{% endblock %}

Always extend through the ``builtin/`` prefix — ``{% extends "landing.html" %}`` resolves to
the override itself and fails with a recursion error. ``landing.html`` and ``project.html``
define the blocks ``title``, ``head``, ``header``, ``content``, and ``footer``;
``simple_root.html`` defines only ``head``, because its body is the :pep:`503` anchor list
that installers parse.

Templates affect the HTML output only. The JSON Simple API output is spec-defined and is
never templated.

.. code-block:: yaml

   templates: ./templates

.. _config-title:

``title``
---------

:Type: string
:Default: ``Package index``
:Constraints: Must be a string.

The heading and ``<title>`` of the landing page (``<out>/index.html``). It has no effect when
``html`` is not in :ref:`config-formats`, because no landing page is written.

.. code-block:: yaml

   title: yourorg package index

.. _config-url:

``url``
-------

:Type: string
:Default: none
:Constraints: Must be a string beginning with ``https://``.

The public base URL the finished site will be served from. When set, the landing page shows a
copy-pasteable install command built from it — the value is stripped of trailing slashes and
``/simple/`` is appended:

.. code-block:: text

   pip install --extra-index-url https://yourorg.github.io/pypi/simple/ PACKAGE

When omitted, the landing page simply links to the simple index without an install example —
unless ``$GITHUB_REPOSITORY`` is set, which supplies one. It is never derived from this file's
own :ref:`config-repositories`; see
:ref:`the URL derivation rules <cli-url-derivation>` for why. The value is not used for
anything else: it does not rewrite asset URLs and it is not required for the index to work.

.. code-block:: yaml

   url: https://yourorg.github.io/pypi/

.. _config-missing-digest:

``missing_digest``
------------------

:Type: string
:Default: ``download``
:Constraints: One of ``download``, ``no-fragment``, ``omit``. **Rejected outright when**
              :ref:`config-assets` **is** ``mirror`` — even if the value equals the default.

GitHub's API supplies a sha256 digest for release assets uploaded since mid-2025. The builder
uses that digest directly and never downloads those files. This key governs only the assets
that have no API digest:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Value
     - Behavior
   * - ``download``
     - Download the asset and hash it, so the link carries a ``#sha256=`` fragment.
   * - ``no-fragment``
     - Index the asset with no ``#sha256=`` fragment; installers skip integrity
       verification for it.
   * - ``omit``
     - Leave the asset out of the index and warn on stderr.

Duplicate filenames are resolved *before* this policy is applied, so if the first
repository's copy of a filename lacks a digest, a later copy's digest is not consulted.

Under mirroring the policy is meaningless — every file is hashed from the bytes actually
downloaded — which is why setting both keys is a configuration error rather than a silent
no-op.

.. code-block:: yaml

   missing_digest: no-fragment

.. _config-formats:

``formats``
-----------

:Type: list of strings
:Default: ``[html, json]``
:Constraints: Must be a non-empty list whose entries are ``html`` or ``json``, with no
              duplicates.

Selects which representations of the index are written.

``html``
   Writes the :pep:`503` HTML tree — ``<out>/simple/index.html`` and
   ``<out>/simple/<project>/index.html`` — plus the human-facing landing page at
   ``<out>/index.html``.

``json``
   Writes the :pep:`691` JSON Simple API — ``<out>/simple/index.json`` and
   ``<out>/simple/<project>/index.json`` — at ``api-version`` 1.1, including the :pep:`700`
   ``versions``, ``size``, and ``upload-time`` fields.

``formats: [json]`` therefore produces a headless index with no landing page and no HTML;
``formats: [html]`` produces HTML only. With both (the default) the JSON files sit alongside
the HTML, which static hosts serve as ordinary files; a full webserver can instead serve them
at the canonical URLs through ``Accept``-header content negotiation on
``application/vnd.pypi.simple.v1+json``.

.. code-block:: yaml

   formats: [html, json]

.. _config-mirror:
.. _config-assets:

``assets``
----------

:Type: string
:Default: ``link``
:Constraints: One of ``link``, ``mirror`` or ``redirect``. ``mirror`` cannot be combined with
              :ref:`config-missing-digest`. ``redirect`` requires a
              :ref:`config-target` that provides a redirector — of the built-ins, only
              ``cloudflare`` does — and is the only mode that accepts
              :ref:`config-missing-metadata`. On the command line the equivalent of
              ``assets: mirror`` is the ``--mirror`` flag; passing ``--mirror``
              together with ``--config`` is an error. There is no command line equivalent of
              ``redirect``: it needs a target, and ``--target`` is itself refused with
              ``--config``, so it is a configuration-file mode.

Decides where the file links in the index point, and therefore who serves the packages.

``link``
   The default. Every link points at the asset's own GitHub release URL and no package
   bytes are transferred, except for the digest-less assets :ref:`config-missing-digest`
   asks to hash. GitHub stays in the serving path.

``mirror``
   The builder downloads every indexed asset into ``<out>/files/<project>/`` and rewrites
   the index links to relative paths, so the finished site is self-contained and relocatable
   and GitHub is out of the serving path. This is also how private repositories are indexed:
   downloads go through GitHub's authenticated asset API with the supplied token, whereas
   direct release-asset links would not be fetchable by installers.

``redirect``
   Every link points at a path *the site itself* serves —
   ``../../_assets/<asset-id>/<filename>``, relative to ``simple/<project>/``, so the site
   stays relocatable exactly as under ``mirror``. No package bytes are copied into the site.
   Something on the host has to answer those paths: a token-holding redirector that calls
   GitHub's asset API and hands the client the short-lived signed URL GitHub replies with.
   That is what a target providing a redirector installs, which is why the mode is refused
   unless the selected :ref:`config-target` declares it — see
   :ref:`the exit-1 conditions <cli>` for the message, and :ref:`targets` for the attribute.

   This is the second way to serve **private** repositories, and the only one that serves
   them without copying every asset into the site on every build. It is likewise the only way
   to serve :pep:`658` metadata for a private repository *without mirroring the wheels*:
   under ``link`` the sidecar would have to live at GitHub's own ``<url>.metadata``, which
   the builder cannot write, and under ``mirror`` it is extracted only because every wheel
   has already been copied into the site, while here the sidecar is a static file under a
   path the site owns. :ref:`config-missing-metadata` decides whether the builder fills the
   gaps.

   The build also writes ``<out>/_assets/manifest.json``, the redirector's allow-list of
   published assets. It is served from the site, so a new release needs only a redeploy, and
   it is what keeps a leaked index URL from being turned into a fetch of any asset the
   redirector's token happens to be able to read.

   Note what that file contains: one record per published asset, naming the **source
   repository** as ``owner/name`` alongside the filename. Nothing else the build writes
   carries that — under this mode every index link is a local ``_assets/`` path, and neither
   the HTML nor the JSON emits the source repository — so the manifest is strictly more
   disclosing than the index it sits beside, and must be gated with it. See
   :ref:`howto-private-without-mirroring` for the deployment that does the gating.

Under ``assets: mirror`` every file is hashed while it streams to disk. Downloads are staged
in a ``.part`` file and only replace the destination after the length (when the server
advertises ``Content-Length``) and the advertised digest both check out, so a failed or
interrupted build never corrupts a previously mirrored file. Files already present with the
expected hash are reused, so repeat builds fetch only new assets — but files removed from
releases are **not** pruned from ``<out>/files/``.

When :ref:`config-metadata` is also enabled, core metadata is extracted from every mirrored
wheel and written beside it as ``<filename>.metadata``.

.. code-block:: yaml

   assets: mirror

.. deprecated:: 2026.8.X

   The boolean ``mirror`` key is replaced by ``assets``. ``mirror: true`` still loads as
   ``assets: mirror`` and ``mirror: false`` as ``assets: link``, each printing one line on
   stderr before the build starts::

      warning: index.yml: 'mirror' is deprecated; write assets: mirror

   It is still validated as a boolean, so a quoted ``"true"`` is still an error. Setting
   ``mirror`` **and** ``assets`` in the same file is rejected outright rather than resolved
   in either direction — see :ref:`the validation errors <config-errors>`. The alias will not
   be removed before the 2027.1 release.

.. _config-target:

``target``
----------

:Type: string
:Default: ``static``
:Constraints: Must be a string. The name is *not* checked while the file is validated: the
              set of valid names is not fixed, because installed plugins add to it. The
              command line resolves the name against the registry immediately afterwards,
              before any network request, and exits 1 listing every registered name when
              there is no match. On the command line the equivalent is ``--target``; passing
              ``--target`` together with ``--config`` is an error.

Selects the deployment target — the component that writes the artifacts one particular host
needs *beside* the index, such as a cache-header file or a server configuration snippet. A
target never changes the index itself and never rewrites a URL; that is
:ref:`config-assets`'s job, which is why the same index can be deployed anywhere.

The built-in names are ``static`` (the default, which writes nothing), ``cloudflare``, and
``nginx``. Any distribution installed alongside ``ghr-pypi`` may register more through the
``ghr_pypi.targets`` entry point group. See :ref:`targets` for what each built-in writes,
where it writes it, and how to add your own.

.. code-block:: yaml

   target: cloudflare

.. _config-metadata:

``metadata``
------------

:Type: boolean
:Default: ``true``
:Constraints: Must be ``true`` or ``false``.

Controls :pep:`658` core metadata, which lets resolvers read a wheel's dependencies without
downloading the wheel.

* **Mirror mode** (:ref:`config-assets` ``mirror``): metadata is extracted from each mirrored
  wheel, written next to it as ``<filename>.metadata``, and advertised in the index. A wheel
  that cannot be read produces a warning and is simply advertised without metadata.
* **Link mode**: the index can only advertise a metadata file that already lives at the
  wheel's own URL plus ``.metadata``, so the metadata must be uploaded as a release asset
  named ``<wheel-filename>.metadata``. The builder reports coverage per repository::

     warning: yourorg/lib-one: 3 of 4 wheels have no .metadata asset; resolvers must
     download full wheels for dependency metadata

* **Redirect mode** (:ref:`config-assets` ``redirect``): a release's own ``.metadata`` asset
  is served through the redirector like any other asset. For wheels that have none,
  :ref:`config-missing-metadata` decides between extracting the metadata and warning about
  the gap.

With ``metadata: false`` no ``.metadata`` asset is ever paired, nothing is extracted, nothing
is advertised, and the coverage warnings are suppressed. That includes redirect mode, where it
also makes :ref:`config-missing-metadata` moot — the key is still validated, it simply has
nothing left to decide.

.. code-block:: yaml

   metadata: false

.. _config-missing-metadata:

``missing_metadata``
--------------------

:Type: string
:Default: ``extract``
:Constraints: One of ``extract`` or ``warn``. **Rejected outright unless** :ref:`config-assets`
              **is** ``redirect`` — even if the value equals the default.

What to do about a wheel whose release carries no ``<wheel-filename>.metadata`` asset. Only
redirect mode can do anything about it, because only there does the sidecar's URL point at a
path the build controls.

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Value
     - Behavior
   * - ``extract``
     - Download each such wheel once, read its core metadata, and write the sidecar into
       ``<out>/_assets/<asset-id>/``. The wheel itself is discarded — only the metadata is
       kept — and the index advertises the sidecar's sha256.
   * - ``warn``
     - Download nothing and advertise no metadata for those wheels, reporting coverage per
       repository on stderr, exactly as link mode does::

          warning: yourorg/lib-one: 3 of 4 wheels have no .metadata asset; resolvers must
          download full wheels for dependency metadata

``extract`` is the default because the cost is paid once per wheel, at build time, by one
machine — while the gap it closes is paid by every resolution on every developer's machine,
forever. Reach for ``warn`` when the build minutes matter more: a very large backfill, or a
CI budget that a first full build would blow. The better fix in either case is to publish the
sidecars from the release workflow (:ref:`cli-extract-meta` and
:ref:`howto-publish-metadata`), which makes the question moot — a wheel whose release has a
sidecar is never downloaded under either value.

A wheel that cannot be read produces a warning and is advertised without metadata, exactly as
under mirroring. Sidecars are written under the asset id, not the project name, so nothing
here collides with the mirrored layout.

Setting the key outside redirect mode is an error rather than a silent no-op. Neither other
mode has anywhere to put a sidecar it extracted: mirror mode already extracts one for every
wheel it downloads, and link mode would have to write to GitHub's own
``<url>.metadata``. So a file asking for ``extract`` under ``assets: link`` is asking for
something that cannot happen, and would otherwise be read back later as though it had.

.. code-block:: yaml

   assets: redirect
   missing_metadata: warn

.. _config-yanked:

``yanked``
----------

:Type: mapping of project name to a mapping of version to reason
:Default: ``{}`` — nothing is yanked
:Constraints: Must be a mapping. Project keys must be strings; version keys must be
              **quoted** strings; each reason must be a non-empty string or ``true``.
              ``false`` is rejected outright. Project keys are :pep:`503`-normalized, and
              two keys that normalize to the same project — or two version keys that denote
              the same release — are an error rather than a merge.

Marks released files as yanked per :pep:`592`, without deleting anything from GitHub. A yank
is a signal to installers that a release is defective and should not be selected for new
resolutions, while remaining installable for anyone who asks for it by exact version.

A yanked file is indexed exactly like any other. It keeps its anchor in
``simple/<project>/index.html`` and its entry in ``simple/<project>/index.json``, its version
still appears in the :pep:`700` ``versions`` list, and under :ref:`config-assets` it is still
downloaded and still has its :pep:`658` metadata extracted. The only difference is the marker:

* **HTML** — the anchor gains ``data-yanked="<reason>"``, or ``data-yanked=""`` when the
  reason is ``true``. The attribute is absent for files that are not yanked.
* **JSON** — the file object gains ``"yanked": "<reason>"`` or ``"yanked": true``. The key is
  absent for files that are not yanked.

``pip`` and ``uv`` then skip the release during resolution unless the requirement pins that
exact version (``==1.0.1``, or a lock file, or an equivalent constraint), in which case they
install it and print the reason. Yanking therefore leaves existing pinned installs working;
:ref:`config-exclude` does not.

The reason string is free text and is shown to the user, so make it actionable — ``"broken
sdist, use 1.0.2"`` beats ``"bad"``. Use ``true`` only when there is genuinely nothing to say.

Versions are matched by :pep:`440` equivalence, not by string equality, so ``"1.0"`` in the
config matches a file built as ``1.0.0`` and vice versa. Two caveats follow from that:

* A local version is *not* equivalent to its public version — ``"1.0.0"`` does **not** match
  ``1.0.0+local``. Write local versions out in full: ``"1.0.0+local"``.
* A version string neither side can parse is compared literally, and a file whose version
  cannot be read from its filename at all matches nothing.

Every file of the matched version is yanked — wheel, sdist, and every wheel of every platform.
Yanks are per project and version; there is no way to yank a single file of a release.

.. code-block:: yaml

   yanked:
     demo-lib:
       "1.0.1": broken sdist, use 1.0.2
       "0.9": true              # yanked, no reason given
     demo-app:
       "2.0.0+local": superseded

.. _config-exclude:

``exclude``
-----------

:Type: mapping of project name to a list of version strings
:Default: ``{}`` — nothing is excluded
:Constraints: Must be a mapping. Project keys must be strings; each value must be a list of
              **quoted** version strings. Project keys are :pep:`503`-normalized, and two
              keys that normalize to the same project — or two versions in one list that
              denote the same release — are an error rather than a merge.

Drops the matched files from the build entirely. Unlike :ref:`config-yanked`, an excluded file
is not marked, it is simply not there: no anchor, no JSON entry, no appearance in the
:pep:`700` ``versions`` list, nothing mirrored, and no digest fetched or computed for it.
Anything already pinned to that version stops resolving.

Exclusion happens before the duplicate-filename bookkeeping, so an excluded file never claims
a filename slot: if a later repository in :ref:`config-repositories` publishes the same
filename, that copy is still indexed normally.

Version matching is identical to :ref:`config-yanked` — :pep:`440` equivalence, with the same
local-version and unparseable-version caveats.

Reach for this when the release must not be installable by anyone: a leaked credential, a
wrong-license artifact, an accidental upload. For a merely broken release, prefer ``yanked``.
Excluding does not delete anything from GitHub; the assets stay public, and re-running the
build without the entry brings them straight back.

.. code-block:: yaml

   exclude:
     demo-lib:
       - "0.1.0"
       - "0.2.0"

.. _config-errors:

Validation errors
=================

Every failure below raises ``ConfigError``, which the command line prints prefixed with
``error:`` before exiting with status 1. ``{path}`` is the path passed to ``--config``.
Validation runs in the order listed, so only the first problem is reported.

``cannot read config file {path}: {error}``
   **Cause:** the file could not be opened or read — it does not exist, is a directory, or
   permissions deny it.
   **Fix:** check the path given to ``--config``; it is resolved relative to the working
   directory of the build, which in CI is the repository checkout.

``config file {path} is not valid UTF-8: {error}``
   **Cause:** the file's bytes are not decodable as UTF-8.
   **Fix:** re-save the file as UTF-8.

``invalid YAML in {path}: {error}``
   **Cause:** the file is not well-formed YAML. The wrapped message names the line and
   column.
   **Fix:** correct the syntax — most often inconsistent indentation or an unquoted value
   containing ``:``.

``{path}: top level must be a mapping``
   **Cause:** the document parses but is not a mapping — for example it is a bare list, a
   scalar, or empty.
   **Fix:** make the top level ``key: value`` pairs — an empty file is not a valid
   configuration even though every key is optional.

``{path}: unknown key(s): {names}``
   **Cause:** the mapping contains keys outside the thirteen documented above (and the
   deprecated ``mirror``); the sorted list of offenders is included.
   **Fix:** remove or rename them. Typos such as ``repository:`` or ``mirrors:`` land here.

``{path}: 'repositories' must be a non-empty list``
   **Cause:** ``repositories`` is not a list, or is an empty list (only checked when the key
   is present — an absent key, or one written with no value, falls back to
   ``$GITHUB_REPOSITORY``).
   **Fix:** provide at least one ``OWNER/NAME`` entry, or remove the key entirely.

``{path}: repository {repo!r} is not OWNER/NAME``
   **Cause:** an entry is not a string, or does not split into exactly two non-empty parts on
   ``/`` — ``yourorg``, ``yourorg/``, ``https://github.com/yourorg/repo`` all fail.
   **Fix:** use the bare ``owner/name`` slug, with no URL, no ``.git`` suffix, no trailing
   slash.

``{path}: repository {repo!r} may not use a pattern in the owner``
   **Cause:** an entry puts an ``fnmatch`` metacharacter (``*``, ``?``, ``[``) in the half
   before the ``/`` — ``*/lib``, ``your*/lib``. There is no GitHub endpoint for "every
   organization I can see", so there is nothing to expand it against.
   **Fix:** name the owner in full. One entry per owner; patterns are a name-half feature.

``{path}: 'repositories' contains duplicates``
   **Cause:** two entries are equal ignoring case — ``YourOrg/Lib`` and ``yourorg/lib``
   collide. Patterns are compared as written, so ``yourorg/*`` twice is a duplicate while
   ``yourorg/*`` and ``yourorg/lib-*`` are not, even though they overlap.
   **Fix:** list each repository once. Overlapping *patterns* are fine — they de-duplicate at
   expansion time, first occurrence winning.

``{path}: 'exclude_repositories' must be a list of patterns``
   **Cause:** ``exclude_repositories`` is present but is not a list — a bare string is the
   usual mistake. Unlike ``repositories``, an empty list is accepted.
   **Fix:** write it as a YAML list; a single entry still needs to be one:
   ``- yourorg/legacy``.

``{path}: exclude_repositories entry {value!r} is not OWNER/NAME``
   **Cause:** an entry is not a string, or does not split into exactly two non-empty parts on
   ``/``. A bare name is the usual mistake: exclusions match the whole ``owner/name`` slug, so
   ``legacy-tool`` is not enough — write ``yourorg/legacy-tool``.
   **Fix:** give both halves. To exclude by name across an owner, use ``yourorg/legacy-*``.

``{path}: exclude_repositories entry {value!r} may not use a pattern in the owner``
   **Cause:** as with ``repositories``, the owner half may not contain ``*``, ``?`` or ``[``.
   **Fix:** name the owner in full, once per owner you need to subtract from.

``{path}: 'templates' must be a string path``
   **Cause:** ``templates`` is present but is not a string (a list or mapping, typically).
   **Fix:** give a single path string.

``{path}: templates directory not found: {resolved}``
   **Cause:** the path resolved against the config file's directory is not an existing
   directory. The resolved absolute path is included.
   **Fix:** create the directory or correct the relative path. Remember it is relative to the
   config file, not to the working directory.

``{path}: 'url' must be a string``
   **Cause:** ``url`` is present but is not a string.
   **Fix:** quote it if YAML parsed it as something else.

``{path}: 'url' must be https``
   **Cause:** ``url`` does not start with ``https://``.
   **Fix:** use an ``https://`` URL. Plain ``http://`` is refused because installers should
   not be pointed at an unauthenticated index.

``{path}: 'title' must be a string``
   **Cause:** ``title`` is present but is not a string — an unquoted numeric or date-like
   value is the usual culprit.
   **Fix:** quote the value.

``{path}: 'missing_digest' must be one of download, no-fragment, omit, got {value!r}``
   **Cause:** ``missing_digest`` is not one of the three accepted values.
   **Fix:** use ``download``, ``no-fragment``, or ``omit``. Note ``no-fragment`` uses a
   hyphen, not an underscore.

``{path}: 'formats' must be a non-empty list``
   **Cause:** ``formats`` is present but is not a list, or is an empty list.
   **Fix:** list at least one of ``html`` or ``json``. To emit one format only, write
   ``formats: [json]`` rather than removing the other entry's value.

``{path}: 'formats' entries must be html or json, got {value!r}``
   **Cause:** an entry is something other than ``html`` or ``json``.
   **Fix:** correct the entry.

``{path}: 'formats' contains duplicates``
   **Cause:** the same format is listed twice.
   **Fix:** list each format once.

``{path}: set either 'assets' or the deprecated 'mirror', not both``
   **Cause:** the file contains both keys. Presence is what is checked, so even
   ``assets: mirror`` next to ``mirror: true`` — two spellings of the same thing — is
   rejected. Neither key silently wins: a file that says the same thing twice is one edit
   away from saying two different things, and the resolution order would then be invisible.
   **Fix:** keep ``assets`` and delete ``mirror``.

``{path}: 'mirror' must be true or false``
   **Cause:** the deprecated ``mirror`` key is present but did not parse as a YAML boolean —
   ``"true"`` in quotes, or ``yes`` in YAML 1.2 parsers, land here.
   **Fix:** write ``assets: link`` or ``assets: mirror`` instead; the boolean key is
   deprecated. If you must keep it for now, use an unquoted ``true`` or ``false``.

``{path}: 'assets' must be one of link, mirror, redirect, got {value!r}``
   **Cause:** ``assets`` is present and is none of ``link``, ``mirror`` or ``redirect``. A
   leftover boolean — ``assets: true``, the shape ``mirror`` used to take — lands here.
   **Fix:** use ``link``, ``mirror`` or ``redirect``. Whether the selected
   :ref:`config-target` can *serve* ``redirect`` is not decided here; the command line
   checks that a moment later and exits 1 naming the targets that can. See :ref:`cli`.

``{path}: 'missing_digest' has no effect when 'assets' is mirror``
   **Cause:** ``missing_digest`` is present alongside ``assets: mirror`` (or the deprecated
   ``mirror: true``). The check is on the key's presence, so even
   ``missing_digest: download`` is rejected.
   **Fix:** delete ``missing_digest``. Mirroring hashes every file from the bytes it
   downloads, so there is nothing for the policy to decide.

``{path}: 'metadata' must be true or false``
   **Cause:** ``metadata`` is present but did not parse as a YAML boolean.
   **Fix:** use an unquoted ``true`` or ``false``.

``{path}: 'missing_metadata' must be one of extract, warn, got {value!r}``
   **Cause:** ``missing_metadata`` is not one of the two accepted values. The value is
   checked before the mode is, so a misspelling under ``assets: link`` reports this rather
   than the rejection below.
   **Fix:** use ``extract`` or ``warn``.

``{path}: 'missing_metadata' only applies when 'assets' is redirect; remove it, or set assets: redirect``
   **Cause:** ``missing_metadata`` is present and :ref:`config-assets` is not ``redirect``.
   The check is on the key's presence, so even ``missing_metadata: extract`` — the default —
   is rejected.
   **Fix:** delete the key, or switch to ``assets: redirect``. No other mode has anywhere to
   write an extracted sidecar: mirror mode already extracts one for every wheel, and link
   mode's sidecars would have to live at GitHub's own ``<url>.metadata``.

``{path}: 'target' must be a string``
   **Cause:** ``target`` is present but is not a string — a list, or a mapping written in the
   hope that targets take options.
   **Fix:** give a single registered target name. Whether that name *exists* is not checked
   here; the command line resolves it a moment later and reports
   ``error: unknown target '...'; available: ...`` with every registered name. See
   :ref:`cli` and :ref:`targets`.

``{path}: 'yanked' must be a mapping of project name to a mapping of version to reason``
   **Cause:** ``yanked`` is present but is not a mapping — a list of versions is the usual
   mistake.
   **Fix:** nest it two deep, project first, then version.

``{path}: 'yanked' project keys must be strings, got {project!r}``
   **Cause:** a top-level key under ``yanked`` is not a string — an unquoted numeric or
   date-like project name.
   **Fix:** quote the project name.

``{path}: 'yanked.{project}' must be a mapping of version to reason``
   **Cause:** the value for a project is not a mapping — most often a plain list of versions,
   which is the shape :ref:`config-exclude` wants, not this key.
   **Fix:** give each version a reason, or ``true``.

``{path}: 'yanked.{project}' version keys must be quoted strings, got {version!r}; write it as "{version}"``
   **Cause:** a version key parsed as something other than a string. Unquoted ``1.0`` is a
   YAML float and unquoted ``2`` is an int; both land here.
   **Fix:** quote every version key — ``"1.0"``. The message echoes the quoted form to use.

``{path}: 'yanked.{project}.{version}' is false; remove the entry to un-yank the version``
   **Cause:** a reason of ``false``. There is no such thing as a negative yank: presence in
   the mapping *is* the yank.
   **Fix:** delete the entry. To keep it as a note to yourself, comment it out.

``{path}: 'yanked.{project}.{version}' has an empty reason; use true for a yank with no reason``
   **Cause:** the reason is an empty string. It would render as ``data-yanked=""``, which is
   indistinguishable from a reasonless yank but arrived at by accident.
   **Fix:** write ``true``, or supply a real reason.

``{path}: 'yanked.{project}.{version}' must be a reason string or true, got {reason!r}``
   **Cause:** the reason is neither a string nor ``true`` — a list, a mapping, a number, or
   ``null`` from a version key written with nothing after the colon.
   **Fix:** use a reason string, or ``true``.

``{path}: 'yanked.{project}' has two entries for version {version!r}``
   **Cause:** two version keys under one project denote the same release. Because matching is
   by :pep:`440` equivalence, ``"1.0"`` and ``"1.0.0"`` collide even though YAML sees two
   distinct keys.
   **Fix:** keep one, with the reason you meant.

``{path}: 'yanked' has two entries for project {normalized!r}``
   **Cause:** two project keys normalize to the same name — ``Demo_Lib`` and ``demo-lib`` are
   one project under :pep:`503`. The reported name is the normalized form.
   **Fix:** merge the two mappings under a single key. They are not combined automatically,
   deliberately: silently merging two spellings would hide a typo.

``{path}: 'exclude' must be a mapping of project name to a list of versions``
   **Cause:** ``exclude`` is present but is not a mapping — a bare list of versions, or a list
   of filenames, is the usual mistake.
   **Fix:** key it by project, with a list of versions under each.

``{path}: 'exclude' project keys must be strings, got {project!r}``
   **Cause:** a key under ``exclude`` is not a string.
   **Fix:** quote the project name.

``{path}: 'exclude.{project}' must be a list of version strings``
   **Cause:** the value for a project is not a list — a mapping of version to reason is the
   shape :ref:`config-yanked` wants, not this key.
   **Fix:** use a YAML list. A single version still needs to be one: ``- "1.0.0"``.

``{path}: 'exclude.{project}' versions must be quoted strings, got {version!r}; write it as "{version}"``
   **Cause:** a list entry parsed as something other than a string — unquoted ``1.0`` is a
   float, unquoted ``2`` an int.
   **Fix:** quote every version. The message echoes the quoted form to use.

``{path}: 'exclude.{project}' has two entries for version {version!r}``
   **Cause:** two entries in one project's list denote the same release; as with ``yanked``,
   ``"1.0"`` and ``"1.0.0"`` are the same release.
   **Fix:** list each version once.

``{path}: 'exclude' has two entries for project {normalized!r}``
   **Cause:** two project keys normalize to the same name under :pep:`503`. The reported name
   is the normalized form.
   **Fix:** merge the two lists under a single key.

Mirroring failures raise ``MirrorError`` rather than ``ConfigError``; they are described with
the other runtime failures in :ref:`cli`.

Complete example
================

Every key, annotated. This configuration mirrors two named repositories and a pattern's worth
of others into a self-contained site served from a custom domain, with overridden templates
and both output formats.

.. code-block:: yaml

   # index.yml — passed as: ghr-pypi index --config index.yml --out site

   # Optional. Releases from these repositories are aggregated, in order.
   # On a filename collision the first repository listed wins. Omit the key to
   # index $GITHUB_REPOSITORY instead. The name half may be an fnmatch pattern,
   # whose matches are sorted and spliced in where the pattern stands — so the
   # two explicit entries below stay ahead of everything tools-* brings in.
   repositories:
     - yourorg/lib-one
     - yourorg/lib-two
     - yourorg/tools-*

   # Optional. Subtracts from pattern expansions only: yourorg/lib-one would
   # still be indexed even if an entry here matched it. Entries that match
   # nothing, and duplicate entries, are both accepted deliberately.
   exclude_repositories:
     - yourorg/tools-internal

   # Optional. Landing page heading and <title>.
   title: yourorg package index

   # Optional. Public https base URL of the finished site; drives the install
   # example shown on the landing page (".../simple/" is appended).
   url: https://packages.yourorg.example/

   # Optional. Jinja template overrides, resolved relative to THIS file.
   templates: ./templates

   # Optional. Which representations to write. Both is the default.
   formats: [html, json]

   # Optional. 'mirror' downloads assets into site/files/ and links to them
   # relatively; 'link' (the default) points at GitHub. Either 'mirror' or
   # 'redirect' is required for private repositories. The deprecated spelling
   # of this line is `mirror: true`.
   assets: mirror

   # Optional. Which host artifacts to write beside the site. Default: static,
   # which writes none. Built-ins: static, cloudflare, nginx; plugins add more.
   target: cloudflare

   # Optional. Extract and advertise PEP 658 core metadata. Default: true.
   metadata: true

   # Optional. PEP 592 yanks, keyed by project then version. Version keys must
   # be quoted; the reason is a string, or true for a yank with no reason.
   # Yanked files stay in the index, marked; pinned installs keep working.
   yanked:
     lib-one:
       "1.0.1": broken sdist, use 1.0.2
       "0.9": true

   # Optional. Versions dropped from the build entirely, keyed by project.
   # Nothing is emitted for them and pinned installs stop resolving — use it
   # when the release must not be installable at all.
   exclude:
     lib-two:
       - "0.1.0"

   # NOTE: 'missing_digest' is deliberately absent — it is rejected whenever
   # 'assets' is mirror. Under 'assets: link' it would be valid here:
   #
   #   missing_digest: download   # or no-fragment, or omit
   #
   # NOTE: 'missing_metadata' is absent for the mirror image of that reason —
   # it is rejected unless 'assets' is redirect, where it would be valid:
   #
   #   missing_metadata: extract  # or warn
