.. include:: ./refs.rst
.. role:: big

========
ghr-pypi
========


.. only:: html


    .. image:: https://img.shields.io/badge/License-MIT-blue.svg
        :target: https://opensource.org/licenses/MIT
        :alt: MIT License

    .. image:: https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json
        :target: https://docs.astral.sh/ruff
        :alt: Ruff

    .. image:: https://badge.fury.io/py/ghr-pypi.svg
        :target: https://pypi.python.org/pypi/ghr-pypi/
        :alt: PyPI Version

    .. image:: https://img.shields.io/pypi/pyversions/ghr-pypi.svg
        :target: https://pypi.python.org/pypi/ghr-pypi/
        :alt: Python Versions

    .. image:: https://img.shields.io/pypi/status/ghr-pypi.svg
        :target: https://pypi.python.org/pypi/ghr-pypi
        :alt: Development Status

    .. image:: https://img.shields.io/pypi/types/ghr-pypi.svg
        :target: https://pypi.python.org/pypi/ghr-pypi
        :alt: Typed

    .. image:: https://readthedocs.org/projects/ghr-pypi/badge/?version=latest
        :target: http://ghr-pypi.readthedocs.io/?badge=latest/
        :alt: Documentation Status

    .. image:: https://codecov.io/gh/bckohan/ghr-pypi/branch/main/graph/badge.svg
        :target: https://codecov.io/gh/bckohan/ghr-pypi
        :alt: Code Coverage

    .. image:: https://github.com/bckohan/ghr-pypi/actions/workflows/test.yml/badge.svg?branch=main
        :target: https://github.com/bckohan/ghr-pypi/actions/workflows/test.yml
        :alt: Test Status

    .. image:: https://github.com/bckohan/ghr-pypi/actions/workflows/lint.yml/badge.svg
        :target: https://github.com/bckohan/ghr-pypi/actions/workflows/lint.yml
        :alt: Lint Status


.. The README body is the single source of truth for everything above the toctree.
   Rendering it needs the MyST parser, which needs a Sphinx environment, so doc8 —
   which parses with bare docutils — cannot read this file. It is listed under
   ``ignore-path`` in ``[tool.doc8]``; keep lines here under 100 characters by hand.

.. include:: ../../README.md
   :parser: myst_parser.sphinx_
   :start-after: <!-- docs-index-start -->
   :end-before: <!-- docs-index-end -->

Why this design
===============

Why a static index instead of an index server
---------------------------------------------

An index server — devpi, pypiserver, or a hosted equivalent — is a service: something to
deploy, keep patched, give a certificate, back up, and hand credentials to.
`PEP 503 <https://peps.python.org/pep-0503/>`_ and
`PEP 691 <https://peps.python.org/pep-0691/>`_
describe the Simple API as a set of documents, not as behavior. Installing needs
nothing dynamic; only *uploading* does, and uploading is exactly the part this project does
not do.

So ``ghr-pypi`` writes the documents and stops. The output is a directory: HTML anchor lists,
JSON payloads, and (under ``assets: mirror``) the files themselves. Any static host will serve
it —
GitHub Pages, a CDN, an S3 bucket, an nginx ``root``. Every response is cacheable, there is no
origin process to compromise, no API token sits on the server, and the build is idempotent:
re-running it produces the same site, and rolling back means redeploying a previous one.

Why GitHub release assets are a reasonable package store
--------------------------------------------------------

The alternative to a package store is another package store. Release assets are one you
almost certainly already have: durable, versioned alongside the tag that produced them, served
from GitHub's CDN with the repository's own availability, and access-controlled by the
repository's own permissions. Publishing to them is one step in a release workflow you have
already written.

They are also cheap to index. Since mid-2025 the GitHub API reports a ``sha256`` digest for
each asset, so the builder can advertise a hash without transferring the file. A full rebuild
is a handful of API calls and no package bytes at all — which is why regenerating the whole
index on every release is practical.

The trust model
---------------

Every link the index emits carries a ``#sha256=`` fragment — unless ``missing_digest`` is set
to ``no-fragment`` — and pip and uv refuse a download whose hash does not match. That covers
corruption and tampering between the host and the installer.

The digest itself comes from GitHub's API, so in link mode you are trusting GitHub for
integrity. Mirror mode narrows that: each file is downloaded and hashed locally, the computed
hash is compared against the advertised digest, and a mismatch fails the build — the mirrored
copy is pinned to what was true at build time.

What the index does *not* do: it does not sign anything or attest to who built a wheel; it
does not protect you from a malicious release on a repository you chose to aggregate; and it
does not make a public repository's assets private — they are world-readable whatever the
index says. And ``missing_digest: no-fragment`` deliberately gives verification up for
pre-digest assets, so use it knowingly.

When not to use it
------------------

- **Private repositories without** ``assets: mirror``. Direct asset URLs require an
  ``Authorization`` header that pip will not send. Mirror the files into the site instead and
  put the site behind whatever authentication your host offers.
- **Anything needing an upload API.** There is no ``twine upload`` and no delete. Yanking and
  excluding are configuration, not API calls — the only way to change the index is to rebuild
  and redeploy it.

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   tutorials/index
   how-to/index
   reference/index
   changelog

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
