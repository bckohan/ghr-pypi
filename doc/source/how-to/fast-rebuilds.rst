.. include:: ../refs.rst

.. _howto-fast-rebuilds:

==============================================
How do I make rebuilds fast for a large index?
==============================================

Build time is almost entirely asset downloads. In link mode the goal is to download nothing;
in mirror mode it is to download each file once, ever.

Link mode: avoid hashing downloads
==================================

GitHub's API reports a ``sha256`` digest for assets uploaded since mid-2025, and those files
are never downloaded — the digest goes straight into the ``#sha256=`` fragment. Only assets
*without* an API digest cost anything, and ``missing_digest`` decides what that costs:

.. list-table::
   :header-rows: 1
   :widths: 24 76

   * - Value
     - Cost
   * - ``download`` (default)
     - Downloads and hashes every digest-less asset, on every build.
   * - ``no-fragment``
     - Downloads nothing; those files are indexed without a hash, so installers do not
       verify them.
   * - ``omit``
     - Downloads nothing; those files are left out of the index entirely.

.. code-block:: yaml

   missing_digest: no-fragment

Everything else in link mode is roughly one API request per repository — plus one more per
100 releases it has, and one per owner whose repositories a pattern has to list — so a build
over a dozen repositories with modern assets takes seconds.

Mirror mode: cache the files directory
======================================

Mirroring fetches every asset once. A file already present in ``<out>/files/`` whose hash
matches is reused, so persisting that directory between runs turns a rebuild into
"download only what is new":

.. code-block:: yaml

   - uses: actions/cache@v4
     with:
       path: site/files
       key: mirrored-assets-${{ github.run_id }}
       restore-keys: mirrored-assets-

The ``run_id`` key never hits, so the cache is always written fresh; ``restore-keys`` restores
the most recent previous one. Two things to know:

* Every cached file is **re-hashed** on each build to confirm it is intact. That is local disk
  I/O, not network, but it grows with the size of the mirror.
* Files that disappear from releases are **not** pruned from ``files/``. Clear the directory
  (or drop the cache) when you want them gone.

Smaller odds and ends
=====================

* ``formats: [html]`` or ``formats: [json]`` halves the number of files written.
* ``metadata: false`` skips opening every mirrored wheel to extract its core metadata — worth
  it only if you do not want :pep:`658` metadata at all.
* Releases are read in full, 100 per API request, so a long release history costs one extra
  request per 100 releases. ``yanked`` and ``exclude`` do not shorten that — they filter after
  the fetch — but API calls are the cheap part of a build.

Next
====

* :ref:`config-missing-digest` — the full semantics, and why it is rejected under mirroring.
* :ref:`config-assets` — verification, reuse, and what is never cleaned up.
