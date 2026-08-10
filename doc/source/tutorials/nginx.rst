.. include:: ../refs.rst

.. _tutorial-nginx:

================================
Serve a private index with nginx
================================

In this tutorial you will turn the releases your repositories already publish into a package
index that only your machines can install from, served by nginx on a server you own. The wheels
stay on GitHub — nothing is copied anywhere — and nginx holds the GitHub token, checks a
username and password on every download, and hands the client GitHub's own short-lived signed
URL. At the end you will install one of your own packages from it with ``pip``.

Allow about twenty-five minutes. Everything below runs on **stock nginx**: ``map``,
``auth_basic``, ``try_files`` and ``proxy_pass`` are all compiled in by default, so there is no
njs, no ``auth_request``, and nothing to build from source.

This is the guided version of :ref:`howto-private-without-mirroring`, which is where the
reasoning and the failure symptoms are maintained. The steps below build the thing; that page
explains it.

What you will need
==================

* **One or more GitHub repositories whose release process already attaches wheels to their
  Releases**, with at least one release published. This tutorial does not set that up — it
  starts from the releases you already have.
* **Those repositories may be private, and that is the point.** A private repository's release
  assets need an ``Authorization`` header and an ``Accept: application/octet-stream`` header
  that ``pip`` will not send, so an index that merely links to GitHub is useless for them.
  Everything below exists to put something in the serving path that *can* send those headers.
* **A server you can reach over SSH and use** ``sudo`` **on**, running nginx from your
  distribution's package. The commands below are for Ubuntu 24.04; on anything else the
  package names and one file path change and nothing else does.
* **A DNS name pointing at that server, with a TLS certificate already installed for it.** This
  tutorial calls it ``packages.example.com``; use one you control. Obtaining the certificate is
  not a ``ghr-pypi`` task — whatever issues certificates for your servers already does it — but
  it is not optional either: ``pip`` refuses a plain ``http://`` index, and Basic auth over
  plaintext hands your password to the network.
* **A GitHub token that can read release assets in every repository you index** — a
  `fine-grained personal access token
  <https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens>`_
  with **Contents: Read-only** on each one. This server uses it twice: once to build the index,
  and once, at request time, to fetch each asset on a client's behalf.
* `uv <https://docs.astral.sh/uv/getting-started/installation/>`_ and ``curl`` **on the server**,
  because that is where the build and the checks run, and ``python3`` on whatever machine you
  install from at the end.

Work on the server, in one terminal, for the whole tutorial. Install what nginx needs and set
two variables:

.. code-block:: sh

   sudo apt-get update
   sudo apt-get install -y nginx apache2-utils
   export HOST=packages.example.com
   export GHR_PYPI_TOKEN=<the token you just created>

``apache2-utils`` is only there for ``htpasswd`` in Step 4. Keep this terminal open.

Step 1 — Turn on redirect mode
==============================

Create ``ghr-pypi.yml`` in your home directory on the server, naming the repositories you want
in the index:

.. code-block:: yaml

   repositories:
     - yourorg/private-lib
     - yourorg/private-app
   title: yourorg internal index
   url: https://packages.example.com/
   assets: redirect
   target: nginx

Two keys do the work, and they go together.

``assets: redirect`` changes what the index links to. In the default mode every link points at
GitHub's own asset URL; here every link points at ``../../_assets/<asset-id>/<filename>`` — a
path on *your* site. Nothing serves those paths yet, which is what the second key is for.

``target: nginx`` says what to install at the other end. It writes the nginx configuration that
answers those ``_assets/`` paths by asking GitHub's release asset API for the file, with the
token and the headers ``pip`` cannot send, and returning GitHub's 302 to a signed URL. No
package bytes pass through the server.

Both keys need a configuration file. ``assets: redirect`` has no command line form — ``--mirror``
is shorthand for ``assets: mirror`` and there is no equivalent for this mode — and ``--target``
cannot be passed alongside ``--config``. ``url`` is what the landing page prints in its install
example; nothing fetches it. :ref:`configuration` documents every other key, and
:ref:`config-assets` puts the three asset modes side by side.

Step 2 — Build the index, on the server
=======================================

Make the directory the site will live in, owned by you so the build can write to it, then build:

.. code-block:: sh

   sudo mkdir -p /srv/pypi
   sudo chown "$USER" /srv/pypi
   uvx ghr-pypi index --config ghr-pypi.yml --out /srv/pypi --target-out deploy

Three lines come back, one for the index and one for each file the target wrote::

   wrote index for 2 project(s) to /srv/pypi
   wrote deploy/ghr-pypi.conf for the nginx target
   wrote deploy/ghr-pypi-assets.conf for the nginx target

**Building on the server is not incidental.** The first thing ``ghr-pypi.conf`` sets is
``root``, and its value is the absolute path ``--out`` resolved to *on the machine that ran the
build*. Build the site on your laptop and copy it over and that line names a directory that
exists only on your laptop. Building where the site will live makes it right by construction —
and this server has to hold the GitHub token anyway, for Step 4, so the build is not putting a
secret anywhere it was not already going.

.. note::

   If the build has to happen elsewhere — in CI, say — then either give it an ``--out`` that
   matches where the site lands or fix that one ``root`` line after each copy. It is
   **regenerated on every build**, so it is not a one-time edit.

The two files landed in ``deploy/``, beside the site rather than in it. That is
``--target-out``, which defaults to the working directory: both are operator artifacts, and a
server configuration published with the index would be served to anyone who can reach it.
``ghr-pypi.conf`` is the ``server``-context half — ``root``, directory handling, and the
authenticated ``/_assets/`` location that proxies to GitHub's asset API. ``ghr-pypi-assets.conf``
is the allow-list, a generated ``map`` from every ``_assets/`` URI this build published to the
API path behind it; anything it does not list is a 404.

Both are **regenerated on every build**, and both are commented at length. Those comments are
this target's setup instructions — the paths to create, the ``htpasswd`` invocation, which
``resolver`` to use, where the CA bundle lives on distributions other than Debian — each beside
the directive that needs it. Read them now; everything below walks through what they tell you:

.. code-block:: sh

   less deploy/ghr-pypi.conf

.. note::

   If some of your wheels have no ``.metadata`` sidecar asset, the build downloads each of those
   wheels once to read its core metadata, keeps the metadata and discards the wheel. That is the
   one thing this mode copies, and :ref:`config-missing-metadata` is how to turn it off.

Step 3 — Install the configuration, at two levels
=================================================

Copy both files into ``/etc/nginx/``, which is where the generated configuration expects its
companions:

.. code-block:: sh

   sudo install -m 644 deploy/ghr-pypi.conf deploy/ghr-pypi-assets.conf /etc/nginx/

Now include them — and they go in **two different places**. ``map`` is valid only in ``http``
context while the rest of the snippet is ``server`` context, so one file could not be
``include``\ d at both, which is why the target writes two:

.. code-block:: nginx

   http {
       include /etc/nginx/ghr-pypi-assets.conf;   # http context: the allow-list map

       server {
           include /etc/nginx/ghr-pypi.conf;      # server context: root and locations
       }
   }

Getting that the wrong way round is not subtle: ``nginx -t`` refuses the map inside a ``server``
block with ``[emerg] ... directive is not allowed here``, naming the line of the generated file
it reached first.

Add the http-level include by editing ``/etc/nginx/nginx.conf`` and putting this line inside its
``http { }`` block:

.. code-block:: nginx

   include /etc/nginx/ghr-pypi-assets.conf;

On Debian and Ubuntu ``/etc/nginx/conf.d/*.conf`` is already included from ``http { }``, so
dropping the file there instead works just as well — it is the same context, reached by a
different route.

Then write the server block, replacing the two ``ssl_`` paths with wherever the certificate you
already have actually lives — an ACME client typically puts it under
``/etc/letsencrypt/live/<name>/`` as ``fullchain.pem`` and ``privkey.pem``:

.. code-block:: sh

   sudo tee /etc/nginx/sites-available/pypi > /dev/null <<NGINX
   server {
       listen 443 ssl;
       listen [::]:443 ssl;
       server_name $HOST;

       ssl_certificate     /etc/ssl/certs/$HOST.fullchain.pem;
       ssl_certificate_key /etc/ssl/private/$HOST.key;

       include /etc/nginx/ghr-pypi.conf;
   }
   NGINX
   sudo ln -sf /etc/nginx/sites-available/pypi /etc/nginx/sites-enabled/pypi

That is the whole server block. Everything a package index needs — the document root, the
directory redirects that make the generated relative links resolve, the ``.metadata`` media
type, the authenticated ``_assets/`` location — is in the file you just included.

Step 4 — Create the two files the build will not write
======================================================

The configuration reaches its GitHub token through ``include /etc/nginx/ghr-pypi-token.conf``,
and its passwords through ``auth_basic_user_file /etc/nginx/ghr-pypi.htpasswd``. It creates
neither, and that is deliberate: ``--target-out`` defaults to the working directory, so this
output lands in a build directory and quite plausibly a git repository. **A build tool that put
a credential in its own output would put that credential wherever the output goes.** Cloudflare's
token is a Pages secret, held by the platform and never in a file; nginx has no such vault, so
these two files are yours to write.

.. code-block:: sh

   printf 'proxy_set_header Authorization "Bearer %s";\n' "$GHR_PYPI_TOKEN" \
     | sudo tee /etc/nginx/ghr-pypi-token.conf > /dev/null
   sudo htpasswd -B -c /etc/nginx/ghr-pypi.htpasswd yourname
   sudo chown root:www-data /etc/nginx/ghr-pypi-token.conf /etc/nginx/ghr-pypi.htpasswd
   sudo chmod 640 /etc/nginx/ghr-pypi-token.conf /etc/nginx/ghr-pypi.htpasswd

``htpasswd`` prompts for a password twice; remember it, you will need it in Step 6. ``-c``
*creates* the file and truncates whatever was there, so leave it off when you add a second user.
``-B`` is bcrypt, which nginx can verify only where the platform's ``crypt()`` understands
``$2y$`` — glibc does, so it is right on this server, and the generated comment gives the ``-m``
fallback for platforms where it is not.

Three ways this fails, and where each one surfaces
--------------------------------------------------

All three fail closed. None of them serves an asset to anyone, which is the property that
matters. They do not fail in the *same* way, though, and the difference decides where you find
out:

* **The token file missing** is a parse error. ``include`` is resolved when nginx reads its
  configuration, so ``nginx -t`` fails with ``[emerg] open() "/etc/nginx/ghr-pypi-token.conf"
  failed (2: No such file or directory)`` and nginx will not start. A reload of an
  already-running server is refused too, and it keeps serving its previous configuration. You
  find out on deploy.
* **A wrong** ``proxy_ssl_trusted_certificate`` **path** fails the same way, and deliberately
  so. nginx reads the CA bundle at configuration load, so ``nginx -t`` fails with ``[emerg]
  cannot load certificate ...: BIO_new_file() failed``. The generated path is the Debian, Ubuntu
  and Alpine bundle; the comment beside it lists RHEL, macOS and FreeBSD. Verifying the upstream
  certificate is not optional — nginx does not check one by default, and the request being made
  carries your token.
* **The htpasswd file missing** is neither. ``nginx -t`` **passes**, nginx starts, the index
  pages serve normally, and every asset request answers ``403`` — ``401`` if no credentials were
  offered — with ``open() ... failed`` in the error log. You find out on the first download,
  which is why this is the one an operator actually hits.

Step 5 — Check it and reload
============================

.. code-block:: sh

   sudo nginx -t
   sudo systemctl reload nginx

``nginx -t`` prints ``syntax is ok`` and ``test is successful``. The generated comments say
``nginx -s reload``; on a systemd host ``systemctl reload nginx`` runs exactly that.

Now four requests, still on the server — ``$HOST`` resolves to it, so these go through nginx
exactly as a client's would. Take a real asset URI straight out of the allow-list so you are
asking for something the build actually published:

.. code-block:: sh

   grep -m1 -o '/_assets/[^"]*' /etc/nginx/ghr-pypi-assets.conf

.. code-block:: sh

   export ASSET=<the path that printed>
   curl -s -o /dev/null -w '%{http_code}\n' "https://$HOST/simple/"
   curl -s -o /dev/null -w '%{http_code}\n' "https://$HOST$ASSET"
   curl -sI -u yourname:PASSWORD "https://$HOST$ASSET" | head -1
   curl -s -o /dev/null -u yourname:PASSWORD -w '%{http_code}\n' \
     "https://$HOST/_assets/1/not-a-real-file.whl"

.. code-block:: text

   200
   401
   HTTP/1.1 302 Found
   404

Each number is a different part of the design working.

* ``200`` — the index pages are **not** behind the password; only ``/_assets/`` is. That is the
  opposite of :ref:`tutorial-cloudflare`, where advanced mode puts every request through the
  Worker and a ``401`` here is the design working. Both are defensible — the filenames a private
  index lists are disclosed by any index, and the assets stay gated either way — so this is a
  decision rather than an oversight, and the generated config carries a commented-out
  ``location /simple/`` stanza that closes it if your *project names* are themselves sensitive.
  :ref:`howto-private-without-mirroring` weighs the two.
* ``401`` — the gate. ``pip`` gets this too, and answers it with credentials.
* ``302`` — the point of the whole mode. nginx asked GitHub's asset API with your token and
  handed the reply back unfollowed: a ``location:`` header naming a short-lived signed URL on
  ``release-assets.githubusercontent.com``, which the client fetches itself. The bytes never
  touch your server, and the token never leaves it.
* ``404`` — the allow-list. A URI the build never published is refused even with valid
  credentials, which is what stops a leaked index URL from becoming a fetch of every asset your
  token happens to be able to read.

Step 6 — Install from it
========================

``pip`` and ``uv`` both speak Basic auth, and both look up credentials in ``~/.netrc``
(``_netrc`` on Windows). On the machine you install from, add an entry for the index host using
the user and password you gave ``htpasswd``:

.. code-block:: text

   machine packages.example.com
     login yourname
     password <the htpasswd password>

Then install, with the plain URL and no credentials in it:

.. code-block:: sh

   python3 -m venv /tmp/ghr-pypi-check
   /tmp/ghr-pypi-check/bin/pip install --no-deps \
     --index-url "https://packages.example.com/simple/" <a project name>

``pip`` reports ``Successfully installed``. Four things happened in that one command: ``pip``
read an index page, followed a link to ``_assets/`` on the same host, presented the credentials
and got back GitHub's signed URL, then fetched the file from GitHub with no credentials of its
own and verified it against the ``#sha256=`` fragment the index published.

``--no-deps`` is there because ``--index-url`` replaces PyPI entirely, so a package with
dependencies has nowhere to resolve them from; :ref:`howto-avoid-pypi` covers using your index
and PyPI together. Clean up:

.. code-block:: sh

   rm -rf /tmp/ghr-pypi-check

After every release
===================

Nothing on this server watches GitHub. A new release reaches the index when you rebuild — and
the rebuild is three commands, not one:

.. code-block:: sh

   uvx ghr-pypi index --config ghr-pypi.yml --out /srv/pypi --target-out deploy
   sudo install -m 644 deploy/ghr-pypi.conf deploy/ghr-pypi-assets.conf /etc/nginx/
   sudo nginx -t && sudo systemctl reload nginx

**The reload is not optional, and skipping it fails quietly.** nginx compiles the allow-list
``map`` when it reads its configuration, so a running server keeps serving the map it started
with. Meanwhile the new index pages are served straight from disk the moment the build finishes.
So the site immediately lists the package you just published, every download of that package
answers ``404``, and every older package keeps working — nothing looks broken until someone
tries to install the new one.

This is where the two redirectors' release loops differ. The Cloudflare Worker reads its
allow-list out of the deployed site at request time, so publishing a release there is a redeploy
and nothing else; here the allow-list is part of the configuration, and configuration has to be
reloaded. It is not the only difference between them — the other is what ends up behind the
password, in Step 5 — but it is the only one that can leave a healthy-looking index quietly
404ing its newest package.

Put those three commands in a script and run them from cron, or from the release process that
publishes the wheels. A rebuild is safe to repeat — it overwrites the site and both files — with
one asymmetry worth knowing: the builder never *deletes* anything from ``--out``, so a project
whose last file you removed from GitHub keeps a stale ``simple/<project>/index.html`` in
``/srv/pypi``. Its downloads 404, because the next build drops those URIs from the map, but the
page lingers until you clear the directory. :ref:`howto-deleted-releases` covers what a deletion
does and does not reach.

Mirror instead?
===============

Redirect is one of two answers to the same question, and on a server you own the other one is
genuinely competitive. ``assets: mirror`` downloads every asset into ``site/files/`` at build
time and links to those copies, so nginx serves the packages themselves and never talks to
GitHub again.

.. list-table::
   :header-rows: 1

   * -
     - Redirect
     - Mirror
   * - Where assets live
     - GitHub
     - the server
   * - Token at request time
     - required
     - none
   * - Storage
     - none
     - every wheel, every version
   * - After a release
     - reload
     - resync

**Start with redirect**, which is what this tutorial built: it keeps no copies, needs no
storage, and the token stays in one file that nginx alone can read.

**Switch to mirror** when the index has to keep working while GitHub is unreachable, or when a
long-lived token on the server is not something you are willing to hold, or when you cannot rely
on the reload happening — a mirror that is never resynced serves an older index, which is a
visibly stale index rather than one that 404s the newest package.

Mirroring is a different configuration on the server as well as in the file. The snippet the
``nginx`` target emits under ``assets: mirror`` has **no** ``auth_basic`` in it at all, because
there is no ``_assets/`` location to gate — and ``site/files/`` then *is* your private packages,
so the password becomes yours to add. :ref:`howto-private-repository` covers that end of it.

What you built
==============

A private :pep:`503` and :pep:`691` index on a server you own, where **no package bytes were
copied anywhere**. The wheels are still release assets on the repositories that made them; the
site is a few kilobytes of index pages; and between a client and a download sits stock nginx,
holding the only copy of the GitHub token, checking a password, refusing anything outside a
generated allow-list, and handing back a signed URL that expires. The repositories can be
private, which an index that merely links to GitHub cannot manage at all.

Publishing a release is a rebuild, a copy of two files, and a reload.

Where to go next
================

* :ref:`howto-private-without-mirroring` — the same deployment as a reference: every failure
  symptom, the ``resolver`` line and when to change it, what the manifest discloses, and why
  ``^~`` on the assets location is load-bearing.
* :ref:`howto-json-api` — one URL answering with HTML or with the :pep:`691` JSON API depending
  on what the installer asked for. It is a ``map`` and two locations in the server block you
  wrote in Step 3, and nginx is the host that can do it.
* :ref:`howto-private-repository` — mirroring, the other answer to private packages, in full.
* :ref:`howto-customize-pages` — the landing page, and the response headers each mode sets.
* :ref:`targets` — everything the ``nginx`` target emits in each mode, and how to write one of
  your own.
* :ref:`configuration` and :ref:`cli` document every key, option and error message.
* The tool itself lives at repo_.
