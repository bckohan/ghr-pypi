.. include:: ../refs.rst

.. _howto-deleted-releases:

===============================================
What happens when a release or file is deleted?
===============================================

The index is regenerated from the current state of the releases on every build, so anything
you delete on GitHub is gone from the *next* build's index. Nothing is retroactive: the build
has to run, and the deploy has to replace the old files.

Make sure the build runs
========================

Deleting a release fires a ``release`` event, so a workflow subscribed to it rebuilds by
itself:

.. code-block:: yaml

   on:
     release:
       types: [published, deleted]
     workflow_dispatch:

Removing a single *asset* from a release does not delete the release, so do not count on an
event for it — rebuild by hand (``gh workflow run pages.yml``), or on a schedule. Converting a published release back to a
draft is equivalent to deleting it as far as the index is concerned: draft releases are
skipped, because their assets are not publicly downloadable.

Make sure the deploy replaces the old files
===========================================

The builder overwrites what it writes but never deletes anything from ``--out``. Build into a
fresh directory (which is what CI does anyway) and the question does not arise. If you reuse
a directory, a project whose last file disappeared keeps its stale
``simple/<project>/index.html``, and stale pages get shipped unless the deploy prunes:

.. code-block:: sh

   rsync -av --delete site/ packages.example.com:/srv/pypi/

Under ``assets: mirror``, ``site/files/`` is never pruned either — the deleted asset's mirrored
copy stays on disk (and in any CI cache of that directory) even though the index no longer
links to it. Delete the directory, or the cache, to be rid of it. Expect CDN copies to survive
for as long as the ``Cache-Control`` you set on ``/files/``; that is the price of caching
immutable files for a year.

If you delete *everything*
==========================

The build fails rather than publishing an emptied index::

   error: no package assets found in releases of yourorg/lib-one; refusing to build an
   empty index

Exit status 1, nothing is written, and whatever is already deployed stays up. That is
deliberate: a mistaken deletion — or a token that suddenly cannot see the releases — must not
be able to replace a working index with a blank one.

Next
====

* :ref:`howto-build-failed` — the other reasons a build stops.
* :ref:`config-assets` — reuse and pruning rules for ``files/``.
