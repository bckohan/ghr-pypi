.. include:: ../refs.rst

.. _howto-publish-metadata:

================================================
How do I publish PEP 658 metadata for my wheels?
================================================

If you mirror, you already do — extraction is automatic. In link mode you must upload each
wheel's core metadata as a release asset named ``<wheel-filename>.metadata``, attached to the
**same release** as the wheel.

Mirror mode: nothing to do
==========================

With ``assets: mirror`` and ``metadata: true`` (the default), every mirrored wheel is opened,
its ``*.dist-info/METADATA`` is written beside it as ``<filename>.metadata``, and the index
advertises it with its sha256. A wheel that cannot be read warns and is simply advertised
without metadata.

Link mode: upload the sidecar
=============================

The index can only advertise metadata that lives at the wheel's own URL plus ``.metadata``,
so the file has to be a sibling asset in the same release. Add this to the release workflow,
before the release is created:

.. code-block:: yaml

   - uses: astral-sh/setup-uv@v9

   - name: Extract PEP 658 metadata from wheels
     run: uvx ghr-pypi extract-meta dist/

   - name: Upload the wheels, sdists and metadata
     env:
       GH_TOKEN: ${{ github.token }}
     run: gh release create "$GITHUB_REF_NAME" dist/* --generate-notes

``extract-meta`` writes ``<wheel>.metadata`` beside each wheel in ``dist/``. A wheel it
cannot read fails the job before any sidecar is written. See :ref:`cli` for the rest.

The name must match exactly, and the pairing is done **per release** — a ``.metadata`` asset
uploaded to a different release than its wheel is ignored, because advertising it would point
installers at a URL that 404s. Only wheels are paired; sdists have no core metadata.

Check that it worked
====================

The build reports coverage per repository on stderr::

   warning: yourorg/lib-one: 3 of 4 wheels have no .metadata asset; resolvers must
   download full wheels for dependency metadata

No warning means full coverage. On the deployed site:

.. code-block:: sh

   curl -s https://yourorg.github.io/pypi/simple/lib-one/ | grep -o 'data-core-metadata="[^"]*"'
   curl -s https://yourorg.github.io/pypi/simple/lib-one/index.json | grep -o '"core-metadata"'

Set ``metadata: false`` to switch the whole feature off — no pairing, no extraction, no
advertisement, and no coverage warnings.

Next
====

* :ref:`config-metadata` — both modes in full, including what happens on extraction failure.
* :ref:`howto-customize-pages` — keep the metadata attributes if you replace ``project.html``.
