.. include:: ../refs.rst

.. _howto-yank-a-release:

============================
How do I yank a bad release?
============================

Add it to the ``yanked`` key of the config, with a reason, and rebuild. Nothing is deleted
from GitHub and nothing is deleted from the index — the file keeps its link, and installers
learn that it is defective.

.. code-block:: yaml

   # index.yml
   repositories:
     - yourorg/lib-one

   yanked:
     yourpkg:
       "1.0.1": broken sdist, use 1.0.2

.. code-block:: sh

   ghr-pypi index --config index.yml --out site

The yank takes effect on the *next* build, so make sure the build runs and the deploy replaces
the old files — editing the config does nothing on its own. If your Pages workflow only
triggers on ``release`` events, kick it by hand:

.. code-block:: sh

   gh workflow run pages.yml

There is no ``--yank`` flag: yanking is a property of the index, and the index is rebuilt from
the config, so it lives in the config. That also means it is reviewable and revertible — the
yank is a line in a file, and deleting the line un-yanks the release on the next build.

What a yank actually does
=========================

The file stays in the index in every respect. It keeps its anchor, its ``#sha256=`` fragment,
its :pep:`700` ``versions`` entry, and — under :ref:`config-assets` — its mirrored copy and
extracted :pep:`658` metadata. All that changes is a :pep:`592` marker:

.. code-block:: html

   <a href="https://.../yourpkg-1.0.1.tar.gz#sha256=..."
      data-yanked="broken sdist, use 1.0.2">yourpkg-1.0.1.tar.gz</a>

.. code-block:: json

   {"filename": "yourpkg-1.0.1.tar.gz", "yanked": "broken sdist, use 1.0.2"}

``pip`` and ``uv`` read that marker and stop selecting the release: ``pip install yourpkg``
resolves to 1.0.2 as if 1.0.1 were not published. But a requirement that pins the exact
version — ``yourpkg==1.0.1``, a lock file, a hash-pinned ``requirements.txt`` — still installs
it, and the installer prints your reason:

.. code-block:: text

   WARNING: The candidate selected for download or install is a yanked version:
   'yourpkg' candidate (version 1.0.1 ...) Reason for being yanked: broken sdist, use 1.0.2

That is the whole point of a yank, and the reason to prefer it: nobody new gets the bad
release, and nobody already pinned to it has their build broken today. Write the reason for
the person who will see that warning — ``broken sdist, use 1.0.2`` earns its space,
``bad`` does not. Use ``true`` instead of a string only when there is genuinely nothing to
say.

Yanks are per project and version, so every file of that release is marked: the sdist, and
every wheel of every platform. Versions match by :pep:`440` equivalence, so ``"1.0"`` in the
config matches a file built as ``1.0.0``. Watch out for local versions — ``"1.0.0"`` does
**not** match ``1.0.0+local``, which has to be written out in full.

When a yank is not enough
=========================

Sometimes the release must not be installable by anyone, pinned or not: a leaked credential
baked into a wheel, an artifact under the wrong license, a file uploaded to the wrong
repository. That is what ``exclude`` is for.

.. code-block:: yaml

   exclude:
     yourpkg:
       - "1.0.1"

An excluded file never enters the index at all. There is no anchor, no JSON entry, no
:pep:`700` ``versions`` entry, nothing mirrored, and no digest fetched for it — from the
index's point of view the release does not exist, and anything pinned to it stops resolving.

.. list-table::
   :header-rows: 1
   :widths: 34 33 33

   * -
     - ``yanked``
     - ``exclude``
   * - Appears in the index
     - Yes, marked
     - No
   * - Listed in :pep:`700` ``versions``
     - Yes
     - No
   * - Mirrored / metadata extracted
     - Yes
     - No
   * - ``pip install yourpkg``
     - Picks another version
     - Picks another version
   * - ``pip install yourpkg==1.0.1``
     - Installs it, warns with the reason
     - Fails — no such version
   * - Existing pinned installs
     - Keep working
     - Break

Neither key touches GitHub: the release and its assets stay exactly where they are, and
removing the entry brings the files back on the next build. If you want them gone for real,
delete the assets on GitHub — see :ref:`howto-deleted-releases`.

Excluding is also the only one of the two that changes what a *later* repository can publish:
the file is dropped before the duplicate-filename bookkeeping, so if a second repository in
:ref:`config-repositories` ships the same filename, that copy is indexed normally.

If you override ``project.html``
================================

Replacing ``project.html`` wholesale — rather than extending ``builtin/project.html`` — means
you own the anchor, and a template that does not emit ``data-yanked`` publishes yanked files
as if they were fine. Copy the built-in's conditional, alongside the ``#sha256=`` guard and
the metadata attributes it already needs:

.. code-block:: html+jinja

   <a href="{{ file.url }}{% if file.sha256 %}#sha256={{ file.sha256 }}{% endif %}"
      {%- if file.yanked %} data-yanked="{{ file.yanked if file.yanked is string else '' }}"
      {%- endif %}>{{ file.filename }}</a>

``file.yanked`` is ``False`` when the file is not yanked, the reason string when there is one,
and ``True`` for a yank with no reason — hence the ``is string`` test, which renders
``data-yanked=""`` for the reasonless case. ``exclude`` needs nothing from the template: the
file is not in ``files`` to begin with. The JSON output is never templated, so it carries the
``yanked`` key either way.

Next
====

* :ref:`config-yanked` and :ref:`config-exclude` — full constraints, matching rules, and every
  error message the two keys can produce.
* :ref:`howto-customize-pages` — the other attributes a wholesale ``project.html`` override
  has to keep.
* :ref:`howto-deleted-releases` — what happens when you delete the release instead.
