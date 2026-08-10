.. include:: ../refs.rst

.. _howto-private-repository:

==================================================
How do I serve packages from a private repository?
==================================================

Mirror the assets into the site and serve the site behind your own authentication. A private
repository's release assets need an ``Authorization`` header that ``pip`` will not send, so an
index that merely links to them is useless — ``mirror`` downloads the files through GitHub's
authenticated asset API and links to local copies instead.

.. note::

   There are two answers to this question, and this page is the one that needs nothing of the
   host but a password. The other, :ref:`howto-private-without-mirroring`, copies no package
   bytes at all: it points the index at your own site and has a token-holding redirector
   fetch each asset on demand. It needs a host that can run one — the ``cloudflare`` and
   ``nginx`` targets both ship one — and it exposes each file's source repository in an
   allow-list the mirroring route never writes. Mirroring is the simpler and more portable
   choice; reach for the other when copying every asset on every build is the cost you mind.

Build it
========

Use a token that can read the repository: a `fine-grained personal access token
<https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens>`_
with **Contents: Read-only** on it, or — when the build runs in that same repository's
workflow — the built-in ``github.token``.

.. code-block:: yaml

   # index.yml
   repositories:
     - yourorg/private-lib
   title: yourorg internal index
   url: https://packages.example.com/
   assets: mirror

.. code-block:: sh

   export GHR_PYPI_TOKEN=...
   ghr-pypi index --config index.yml --out site

For a single repository the flag form is equivalent:

.. code-block:: sh

   ghr-pypi index yourorg/private-lib --out site --mirror

but that form fixes the landing page's install example to the repository's GitHub Pages URL
(``https://<owner>.github.io/<name>/``). If the index is hosted anywhere else, use a config
file and set ``url`` to the real address. ``--mirror`` cannot be combined with ``--config``;
set ``assets: mirror`` in the file instead.

The result is self-contained: the packages land in ``site/files/<project>/``, every link
becomes a relative path, and each file is hashed from the bytes actually downloaded. Nothing
in the output contains the token.

Serve it
========

Mirroring moves the bytes; it does not protect them. Whatever host you use must require
credentials, because ``site/files/`` now *is* your private packages. GitHub Pages will not do
here — a Pages site built from a private repository is public unless your plan supports
`changing the visibility of a Pages site
<https://docs.github.com/en/pages/getting-started-with-github-pages/changing-the-visibility-of-your-github-pages-site>`_.

Put it behind HTTP basic auth — ``auth_basic`` and ``auth_basic_user_file`` on the whole
``server`` block, since under mirroring the packages are files on that host. The
:ref:`nginx tutorial <tutorial-nginx>` builds the server block, though it gates only
``/_assets/`` because it serves the other mode. Then install with credentials:

.. code-block:: sh

   pip install --index-url https://user:pass@packages.example.com/simple/ yourpkg

A password in a URL leaks into shell history and logs. Put it in ``~/.netrc`` instead and use
the plain URL — ``pip`` and ``uv`` both read that file.

Next
====

* :ref:`config-assets` — what mirroring verifies, what it reuses, what it never cleans up.
* :ref:`howto-private-without-mirroring` — the same goal without copying the assets.
* :ref:`cli` — the ``--token``/``GHR_PYPI_TOKEN`` rules and every failure mode.
