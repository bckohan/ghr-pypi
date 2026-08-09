.. include:: ../refs.rst

.. _howto-json-api:

===================================================
How do I serve the JSON API at the canonical URLs?
===================================================

The builder already writes the :pep:`691` JSON — ``simple/index.json`` and
``simple/<project>/index.json``, api-version 1.1 — whenever ``json`` is in ``formats`` (it is
by default). Serving it at the *canonical* URL, ``/simple/<project>/``, needs a server that
can look at the request's ``Accept`` header. Static hosts cannot, so on GitHub Pages the JSON
is reachable only at its own ``index.json`` address.

nginx
=====

A ``map`` turns the ``Accept`` header into a filename and ``index`` accepts that variable:

.. code-block:: nginx

   map $http_accept $pypi_index {
       default                                      index.html;
       "~*application/vnd\.pypi\.simple\.v1\+json"  index.json;
   }

   location /simple/ {
       index $pypi_index;
       add_header Vary "Accept" always;
   }

   # A JSON page must carry the PEP 691 media type, not application/json.
   location ~ ^/simple/.*index\.json$ {
       types { }
       default_type application/vnd.pypi.simple.v1+json;
       add_header Vary "Accept" always;
   }

The ``map`` block belongs at ``http`` level, which is where ``/etc/nginx/conf.d/*.conf`` is
included; the two ``location`` blocks go in the ``server`` block beside the ``include`` of a
generated ``ghr-pypi.conf``, if you have one. The :ref:`nginx tutorial <tutorial-nginx>` builds
that server block, and :ref:`targets` explains why the generated snippet stops short of this.

Edge functions
==============

On Cloudflare, a `Pages Function <https://developers.cloudflare.com/pages/functions/>`_ or a
`Worker <https://developers.cloudflare.com/workers/>`_ can do the same job in a few lines:
read ``Accept``, and when it names ``application/vnd.pypi.simple.v1+json``, fetch the sibling
``index.json`` and return it with that media type and a ``Vary: Accept`` header. Other CDNs
have equivalent hooks.

Two rules whatever you use
==========================

* The response **must** be typed ``application/vnd.pypi.simple.v1+json``. Served as
  ``application/json`` it is not a Simple API response.
* Send ``Vary: Accept`` so caches do not hand an HTML page to an installer, or vice versa.

Keep ``html`` in ``formats`` unless you are certain every consumer speaks :pep:`691`;
``formats: [json]`` leaves nothing for a browser — or for a static host — to serve at
``/simple/``.

Next
====

* :ref:`config-formats` — what each format writes, and what the landing page needs.
* :ref:`tutorial-nginx` — an nginx server block built end to end, which the ``map`` and the two
  ``location`` blocks above drop straight into.
