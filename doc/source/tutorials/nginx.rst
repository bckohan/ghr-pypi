.. include:: ../refs.rst

.. _tutorial-nginx:

==========================
Serve the index with nginx
==========================

In this tutorial you will write a small Python package, publish it as a GitHub release, build
a self-contained package index on your laptop, ship it to a server with ``rsync``, and serve
it with nginx. Along the way you will teach nginx to do the one thing a static host cannot:
answer the same URL with HTML or with the :pep:`691` JSON API depending on what the installer
asked for. Then you will put the whole index behind a password. At the end you will install
your package from it with ``pip``, twice — once open, once authenticated.

Allow about forty-five minutes. You do not need to have configured nginx before. Follow the
steps in order, type every command exactly as it is written, and at the end you will have
installed a package from an index you built and now run yourself.

What you will need
==================

* A GitHub account.
* ``git``.
* The `GitHub CLI <https://cli.github.com/>`_, signed in — run ``gh auth login`` once.
* `uv <https://docs.astral.sh/uv/getting-started/installation/>`_.
* ``python3`` and ``rsync`` on your laptop.
* A server running Ubuntu 24.04 that you can reach over SSH and use ``sudo`` on, with ports
  80 and 443 open to the internet.
* A DNS name pointing at that server. This tutorial calls it ``packages.example.com``; a
  certificate cannot be issued without a real name, so use one you control.

Set two variables in the terminal you will use for the whole tutorial:

.. code-block:: sh

   export OWNER=$(gh api user --jq .login)
   export HOST=packages.example.com

Replace ``packages.example.com`` with your own DNS name. Check both:

.. code-block:: sh

   echo "$OWNER $HOST"

Keep this terminal open.

Step 1 — Create the package
===========================

Make a directory, start a git repository in it, and create the package layout:

.. code-block:: sh

   mkdir hello-index
   cd hello-index
   git init -b main
   mkdir -p src/hello_index

Create ``pyproject.toml`` with exactly this content:

.. code-block:: toml

   [build-system]
   requires = ["hatchling"]
   build-backend = "hatchling.build"

   [project]
   name = "hello-index"
   version = "1.0.0"
   description = "A package installed from a GitHub release asset"
   requires-python = ">=3.9"

   [tool.hatch.build.targets.wheel]
   packages = ["src/hello_index"]

Create ``src/hello_index/__init__.py``:

.. code-block:: python

   """A very small package, published from a GitHub release asset."""

   __version__ = "1.0.0"


   def greet(source: str) -> str:
       """Return a greeting naming where this package was installed from."""
       return f"Hello from {source}!"

Step 2 — Push the repository to GitHub
======================================

.. code-block:: sh

   git add .
   git commit -m "hello-index 1.0.0"
   gh repo create hello-index --public --source=. --push

Step 3 — Publish the first release
==================================

Build the distributions and attach them to a GitHub release:

.. code-block:: sh

   uv build
   gh release create v1.0.0 dist/* --title "v1.0.0" --notes "First release"
   gh release view v1.0.0

The ``ASSETS`` section lists ``hello_index-1.0.0-py3-none-any.whl`` and
``hello_index-1.0.0.tar.gz``. Those two files are what the index will be built from.

Step 4 — Build the index on your laptop
=======================================

The index is described by a small YAML file. Write it next to the project — it holds no
secrets:

.. code-block:: sh

   cat > index.yml <<EOF
   repositories:
     - $OWNER/hello-index
   title: hello-index package index
   url: https://$HOST/
   assets: mirror
   EOF

``assets: mirror`` is what makes the site self-contained: instead of linking back to GitHub,
``ghr-pypi`` downloads every release asset into ``site/files/`` and rewrites the index links
to point at those copies. Your server then serves the packages as well as the index. ``url``
is the address the finished site will live at; it is used for the install example printed on
the landing page.

``ghr-pypi`` always needs a GitHub token, even for a public repository, because
unauthenticated API requests are rate limited far too aggressively to build an index with.
The ``gh`` CLI already has one:

.. code-block:: sh

   export GITHUB_TOKEN=$(gh auth token)
   uvx ghr-pypi index --config index.yml --out site

It finishes with::

   wrote index for 1 project(s) to site

Look at what it wrote:

.. code-block:: sh

   find site -type f | sort

.. code-block:: text

   site/files/hello-index/hello_index-1.0.0-py3-none-any.whl
   site/files/hello-index/hello_index-1.0.0-py3-none-any.whl.metadata
   site/files/hello-index/hello_index-1.0.0.tar.gz
   site/index.html
   site/simple/hello-index/index.html
   site/simple/hello-index/index.json
   site/simple/index.html
   site/simple/index.json

Three kinds of file, and each one gets its own treatment in the nginx configuration later.
``files/`` holds the mirrored distributions, hashed as they were downloaded, plus the wheel's
:pep:`658` core metadata extracted into a ``.metadata`` sidecar. ``simple/`` holds the index
itself, written twice: once as :pep:`503` HTML and once as the :pep:`691` JSON API.
``index.html`` at the top is the human-facing landing page.

.. note::

   ``--mirror`` is a flag of the command line form only. With ``--config`` it must be set in
   the file, as it is here; passing both is an error. :ref:`configuration` explains why.

Step 5 — Prepare the server
===========================

Open a second terminal and connect to the server:

.. code-block:: sh

   ssh packages.example.com

Use your own DNS name, prefixed with ``user@`` if your login on the server differs from your
local one. Once you are on the server, set the same variable there and install what you need:

.. code-block:: sh

   export HOST=packages.example.com
   sudo apt-get update
   sudo apt-get install -y nginx apache2-utils certbot python3-certbot-nginx

Confirm that this nginx was built with the module that serves pre-compressed files:

.. code-block:: sh

   nginx -V 2>&1 | tr ' ' '\n' | grep gzip_static

It prints ``--with-http_gzip_static_module``. Now create the directory the site will live in,
owned by you so that ``rsync`` can write to it:

.. code-block:: sh

   sudo mkdir -p /srv/pypi
   sudo chown "$USER" /srv/pypi
   sudo chmod 755 /srv/pypi

Leave this terminal connected.

Step 6 — Ship the site to the server
====================================

Back in the first terminal, on your laptop. nginx can serve a ``.gz`` file that was compressed
ahead of time instead of compressing on every request, which is free speed for a directory of
text files that changes only when you publish. Compress the index pages, keeping the
originals:

.. code-block:: sh

   find site -type f \( -name '*.html' -o -name '*.json' \) -exec gzip -9 -k -f {} +

The wheels are already compressed archives, which is why they are left alone. Now copy the
whole directory across:

.. code-block:: sh

   rsync -av --delete site/ "$HOST":/srv/pypi/

The trailing slash on ``site/`` matters: it copies the *contents* of ``site`` into
``/srv/pypi``, not the directory itself. ``--delete`` removes files on the server that are no
longer in the build, so re-running this command is always safe and always leaves the server
matching your laptop exactly.

Step 7 — Configure nginx
========================

In the server terminal, write the configuration:

.. code-block:: sh

   sudo tee /etc/nginx/conf.d/pypi.conf > /dev/null <<'NGINX'
   # Chooses which file to serve for a directory under /simple/, based on what
   # the client said it could accept. PEP 691 installers ask for the JSON media
   # type; browsers and everything else fall through to the HTML default.
   map $http_accept $pypi_index {
       default                                      index.html;
       "~*application/vnd\.pypi\.simple\.v1\+json"  index.json;
   }

   server {
       listen 80;
       listen [::]:80;
       server_name packages.example.com;

       root /srv/pypi;
       index index.html;

       # Serve the *.gz files written by the build instead of compressing on
       # the fly.
       gzip_static on;

       # The simple index. This is the line that does content negotiation: the
       # index file name is a variable, resolved per request by the map above.
       location /simple/ {
           index $pypi_index;
           add_header Cache-Control "public, max-age=300" always;
           add_header Vary "Accept" always;
       }

       # A JSON page must carry the PEP 691 media type, not application/json,
       # or installers will assume it is HTML and fail to parse it. An empty
       # types block drops the inherited MIME table for this location only, so
       # default_type applies.
       location ~ ^/simple/.*index\.json$ {
           types { }
           default_type application/vnd.pypi.simple.v1+json;
           add_header Cache-Control "public, max-age=300" always;
           add_header Vary "Accept" always;
       }

       # PEP 658 core metadata sidecars: an extension nginx has no type for.
       location ~ ^/files/.*\.metadata$ {
           types { }
           default_type application/octet-stream;
           add_header Cache-Control "public, max-age=31536000, immutable" always;
       }

       # A published wheel or sdist never changes, so it can be cached forever.
       location /files/ {
           add_header Cache-Control "public, max-age=31536000, immutable" always;
       }
   }
   NGINX

Put your own name into it, check the syntax, and load it:

.. code-block:: sh

   sudo sed -i "s/packages.example.com/$HOST/" /etc/nginx/conf.d/pypi.conf
   sudo nginx -t
   sudo systemctl reload nginx

``nginx -t`` prints ``syntax is ok`` and ``test is successful``.

Four directives carry the weight here. `map
<https://nginx.org/en/docs/http/ngx_http_map_module.html#map>`_ turns the request's ``Accept``
header into a filename; it has to sit outside the ``server`` block, which is fine because
``/etc/nginx/conf.d/*.conf`` is included inside ``http``. `index
<https://nginx.org/en/docs/http/ngx_http_index_module.html#index>`_ accepts a variable as the
file name, which is what makes negotiation possible without any code. `types
<https://nginx.org/en/docs/http/ngx_http_core_module.html#types>`_ used as an empty block,
paired with ``default_type``, forces one media type for everything a location serves —
declaring ``types`` inside a ``server`` block instead would silently replace the whole
inherited MIME table. `gzip_static
<https://nginx.org/en/docs/http/ngx_http_gzip_static_module.html#gzip_static>`_ makes nginx
prefer ``index.html.gz`` when the client accepts gzip.

.. note::

   You are writing this configuration by hand so that you can see what every directive is
   for. ``ghr-pypi`` can now generate a starting point instead: adding ``target: nginx`` to
   ``index.yml`` writes a ``ghr-pypi.conf`` snippet to ``include`` inside a ``server``
   block, covering ``root``, directory URLs and the ``.metadata`` type. As with mirroring,
   it is a key rather than a flag here — ``--target`` is refused alongside ``--config``. The
   snippet does *not* cover content negotiation, caching or authentication — the three
   things the rest of this tutorial is about — so keep reading. See :ref:`targets`.

Step 8 — Get a certificate
==========================

``pip`` will not install from a plain ``http://`` index, and neither should you. Certbot asks
for an email address, has you agree to the terms, then edits the configuration you just wrote
so that it also listens on 443 with a certificate, and adds a redirect from port 80:

.. code-block:: sh

   sudo certbot --nginx -d "$HOST"

When it asks whether to redirect HTTP traffic to HTTPS, choose redirect. It finishes with
``Successfully received certificate`` and reloads nginx for you. Confirm:

.. code-block:: sh

   curl -sI "https://$HOST/" | head -1

.. code-block:: text

   HTTP/1.1 200 OK

Step 9 — Install your package from your index
=============================================

Back on your laptop. This is the point of the whole exercise:

.. code-block:: sh

   python3 -m venv /tmp/hello-index-check
   /tmp/hello-index-check/bin/pip install --index-url "https://$HOST/simple/" hello-index

``pip`` reports::

   Successfully installed hello-index-1.0.0

Nothing in that install touched PyPI and nothing touched GitHub. ``--index-url`` replaced
PyPI, ``pip`` read your ``simple/`` pages, downloaded the wheel from ``/files/`` on your own
server, and verified the sha256 that ``ghr-pypi`` computed while mirroring it. Prove the
package works:

.. code-block:: sh

   /tmp/hello-index-check/bin/python -c \
     "import hello_index; print(hello_index.greet('nginx'))"

.. code-block:: text

   Hello from nginx!

Step 10 — Ask the same URL for JSON
===================================

Here is what the nginx configuration bought you. Ask for the project page the way a browser
would:

.. code-block:: sh

   curl -sI "https://$HOST/simple/hello-index/" | grep -i '^content-type'

.. code-block:: text

   content-type: text/html

Now ask for it the way a :pep:`691` installer does, at the very same URL:

.. code-block:: sh

   curl -sI -H 'Accept: application/vnd.pypi.simple.v1+json' \
     "https://$HOST/simple/hello-index/" | grep -i '^content-type'

.. code-block:: text

   content-type: application/vnd.pypi.simple.v1+json

And read the body. The document has ``meta``, ``name``, ``versions`` and ``files``; this
prints the first entry of ``files``:

.. code-block:: sh

   curl -s -H 'Accept: application/vnd.pypi.simple.v1+json' \
     "https://$HOST/simple/hello-index/" \
     | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["files"][0], indent=4))'

The wheel's entry looks like this — the sdist has one of its own, listed alongside it:

.. code-block:: json

   {
       "core-metadata": {
           "sha256": "1e0c9f..."
       },
       "dist-info-metadata": {
           "sha256": "1e0c9f..."
       },
       "filename": "hello_index-1.0.0-py3-none-any.whl",
       "hashes": {
           "sha256": "5b8a72..."
       },
       "size": 1487,
       "upload-time": "2026-08-06T17:04:11Z",
       "url": "../../files/hello-index/hello_index-1.0.0-py3-none-any.whl"
   }

One URL, two representations, chosen by the client. On a static host the JSON is reachable
only at its own ``index.json`` address; here it is served where :pep:`691` says it should be.
``core-metadata`` points at the sidecar file that mirroring extracted, which lets a resolver
read your package's dependencies without downloading the wheel; ``dist-info-metadata`` is the
same value under the older name, emitted for installers that still look for it.

Step 11 — Put the index behind a password
=========================================

An index you run yourself does not have to be public. In the server terminal, create a
password file and lock it down so only nginx can read it:

.. code-block:: sh

   sudo htpasswd -c /etc/nginx/pypi.htpasswd builder
   sudo chown root:www-data /etc/nginx/pypi.htpasswd
   sudo chmod 640 /etc/nginx/pypi.htpasswd

``htpasswd`` prompts for a password twice. Choose one made only of letters and digits — you
are about to put it in a URL, and other characters would have to be percent-encoded.

Open the configuration:

.. code-block:: sh

   sudo nano /etc/nginx/conf.d/pypi.conf

Certbot rewrote the ``server`` block you created so that it now contains ``listen 443 ssl;``,
and added a second, small block that redirects port 80. Find the block with ``listen 443
ssl;`` in it and add these two lines directly below its ``index index.html;`` line:

.. code-block:: nginx

   auth_basic           "package index";
   auth_basic_user_file /etc/nginx/pypi.htpasswd;

Save, check, and reload:

.. code-block:: sh

   sudo nginx -t
   sudo systemctl reload nginx

Because `auth_basic <https://nginx.org/en/docs/http/ngx_http_auth_basic_module.html#auth_basic>`_
is set on the whole ``server`` block, every location inherits it — the landing page, the
index pages, and the mirrored files alike. Back on your laptop, confirm the index is now
closed:

.. code-block:: sh

   curl -sI "https://$HOST/simple/" | head -1

.. code-block:: text

   HTTP/1.1 401 Unauthorized

And that it opens for you. Replace ``PASSWORD`` with the one you chose:

.. code-block:: sh

   /tmp/hello-index-check/bin/pip install --force-reinstall \
     --index-url "https://builder:PASSWORD@$HOST/simple/" hello-index

``pip`` reports ``Successfully installed hello-index-1.0.0`` again. It sent the credentials
to the index pages and reused them for the download from ``/files/``, because both are on the
same host.

Clean up the throwaway environment:

.. code-block:: sh

   rm -rf /tmp/hello-index-check

A password in a URL ends up in shell history, in ``pip``'s own log output, and in your
server's access log. For anything you use regularly, put the credentials in ``~/.netrc``
instead and give ``pip`` the plain ``https://packages.example.com/simple/`` URL; ``pip`` and
``uv`` both read that file.

What you built
==============

A package index you run: a directory of static files, mirrored from GitHub releases and
verified by sha256 on the way in, served over TLS by nginx with long-lived caching on the
immutable files, short-lived caching on the index, pre-compressed transfers, real :pep:`691`
content negotiation, and a password on the door. Rebuilding it is two commands — ``uvx
ghr-pypi index --config index.yml --out site`` and the ``rsync`` — which is a shape that drops
straight into a cron job or a CI workflow.

Where to go next
================

* The :ref:`how-to guides <how-to>` answer the questions that come next: aggregating several
  repositories into one index, indexing a private repository, customizing the landing page.
* :ref:`configuration` documents every key of the YAML configuration file you wrote in step
  4, including the ``formats`` key — set it to ``[json]`` and the HTML disappears entirely,
  leaving nginx serving nothing but the :pep:`691` API.
* :ref:`config-assets` explains exactly what mirroring verifies, what it reuses between
  builds, and what it does not clean up.
* :ref:`cli` documents every command line option, every exit code, and every error message.
* The tool itself lives at repo_.
