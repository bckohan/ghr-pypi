.. include:: ../refs.rst

.. _howto-other-hosts:

==========================================================
How do I host the index somewhere other than GitHub Pages?
==========================================================

Nothing about the output is GitHub-specific: ``--out`` is a directory of static files, and any
host that serves it over https will do. Use a config file so that ``url`` names the real host,
and set ``assets: mirror`` if you want the packages served from there too.

.. code-block:: yaml

   # index.yml
   repositories:
     - yourorg/lib-one
   title: yourorg package index
   url: https://packages.example.com/
   assets: mirror        # optional: also serve the wheels yourself

.. code-block:: sh

   ghr-pypi index --config index.yml --out site

Set ``url`` explicitly rather than letting it default. Left unset in a config file it comes
out empty — no install example at all — unless the build runs in GitHub Actions, where
``$GITHUB_REPOSITORY`` supplies a ``https://<owner>.github.io/<name>/`` Pages address that
would be a lie anywhere else. :ref:`The URL derivation rules <cli-url-derivation>` spell out
exactly when.

What the host has to do
=======================

* **Serve ``index.html`` for directory URLs.** ``pip`` requests ``/simple/`` and
  ``/simple/<project>/`` with a trailing slash. Static-website hosting on an object store
  needs its index document set to ``index.html``; a bare bucket behind a CDN does not do this
  by default.
* **Speak https.** ``url`` is required to be https, and installers should never be pointed at
  an unauthenticated index.
* **Serve ``.metadata`` files** (mirror mode only). Any sensible type works — ``text/plain``
  or ``application/octet-stream``; some servers have no mapping for the extension and refuse
  to serve it at all.
* **Cache appropriately.** ``/files/`` is immutable and can be cached for a year; ``/simple/``
  changes on every release and wants a short TTL.

A :ref:`deployment target <targets>` can write some of this for you. Under ``assets: link``
and ``assets: mirror``, ``target: cloudflare`` emits a ``_headers`` file covering both of the
last two bullets — the cache rules and the ``.metadata`` content type. (Under
``assets: redirect`` it emits a Worker instead, which sets those headers itself; see
:ref:`howto-private-without-mirroring`.) ``target: nginx`` emits a server snippet covering
the first and third — directory URLs and the ``.metadata`` type — but **no caching directives
at all**; add those yourself. If your host is neither, :ref:`howto-write-a-target` shows how
to add one.

Shipping the directory
======================

.. code-block:: sh

   rsync -av --delete site/ packages.example.com:/srv/pypi/
   aws s3 sync site/ s3://your-bucket/ --delete

``--delete`` in both: it is what removes projects and files that no longer exist. Cloudflare
Pages, Netlify and similar services take the directory as a build output instead — point their
build command at ``ghr-pypi index`` and their output directory at ``site``.

With ``assets: mirror`` every link in the index is relative (``../../files/...``), so the site
can be moved between hosts and prefixes without rebuilding. Without it, the index links back
to GitHub's asset URLs and your host only ever serves a few kilobytes of text.

Next
====

* :ref:`tutorial-cloudflare` — a mirrored index built and served by Cloudflare Pages.
* :ref:`tutorial-nginx` — a server you own, with content negotiation and a password.
* :ref:`config-url` and :ref:`config-assets` — the two keys that matter when relocating.
* :ref:`targets` — the host artifacts ``ghr-pypi`` can generate for you, and how to add one.
