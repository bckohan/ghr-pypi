.. include:: ../refs.rst

.. _targets:

==================
Deployment targets
==================

A **target** writes the files one particular host needs *beside* the index — a cache-header
file, a server configuration snippet — and nothing else. It is selected with the
:ref:`config-target` key or the ``--target`` option, it runs once, after the site is on disk,
and it is handed a read-only description of the finished build.

What a target does **not** do is rewrite URLs. Where the index's links point is decided by
:ref:`config-assets`, and only by it. Keeping the two axes apart is what lets the same index
be deployed anywhere: ``assets`` decides what the site *is*, ``target`` decides what the host
needs to serve it. It is also what keeps targets cheap to write — a new target never has to
understand link rewriting, mirroring, or :pep:`658` sidecars.

The two axes meet in exactly one place, described under :ref:`targets-redirect`:
``assets: redirect`` points every link at a path the *host* must answer, so it is refused
unless the selected target says it can.

The default target is ``static``, which writes nothing. "No deployment artifacts" is a named
choice rather than an absence, so the command line always has a target to call.

The protocol
============

A target is any object with a ``name`` string and an ``emit`` method. There is no base class
to inherit and nothing to call at import time; ``Target`` is a :class:`typing.Protocol`, so a
plain class in your own package satisfies it structurally. Everything below lives in
``ghr_pypi.targets``, except :class:`~ghr_pypi.index.FileEntry`, which is what
``SiteContext.projects`` is made of.

.. autoclass:: ghr_pypi.targets.Target
   :members:

.. autoclass:: ghr_pypi.targets.SiteContext
   :members:

.. autoclass:: ghr_pypi.index.FileEntry

.. autofunction:: ghr_pypi.targets.get_target

.. autofunction:: ghr_pypi.targets.available_targets

.. _targets-redirect:

``supports_redirect``
=====================

An **optional** class attribute, not a member of the protocol. Set it to ``True`` to declare
that this target installs something on the host capable of answering the ``_assets/`` paths
:ref:`config-assets` ``redirect`` links to:

.. code-block:: python

   class YourTarget:
       name = "yours"
       supports_redirect = True

The command line reads it as ``getattr(target, "supports_redirect", False)`` and, under
``assets: redirect``, refuses any target that does not declare it — exiting 1 with
``error: target '...' cannot serve 'assets: redirect'; targets that can: ...``, listing every
registered target that does. A target that never sets the attribute is therefore never handed
a mode it cannot serve, and never sees ``SiteContext.assets == "redirect"``.

Leaving it out of :class:`~ghr_pypi.targets.Target` is deliberate. A ``@runtime_checkable``
protocol checks that every named attribute *exists*, so adding this one would make
``isinstance(obj, Target)`` **false** for every target that does not set it — including
``static``, and every plugin written before the mode existed. The registry's own tests assert
that ``isinstance`` holds for a built-in, and they would be the first thing to break. An
optional attribute read defensively keeps the protocol describing what every target must have,
and lets a capability be something a target opts into.

The attribute says nothing about *how* the host serves those paths, and the builder does not
care: it writes the links and the manifest, and the target is responsible for the rest. Two
built-ins declare it — ``cloudflare`` with a Worker, ``nginx`` with a ``proxy_pass`` location
on stock nginx — and the two answer the same paths by entirely different means. See
:ref:`howto-private-without-mirroring` for both, deployed.

.. _targets-out-dir:

``out_dir`` versus ``target_dir``
=================================

:class:`~ghr_pypi.targets.SiteContext` carries two directories, and choosing the wrong one is
the mistake with consequences:

``out_dir``
   The site — the value of ``--out``. **Everything under it is published.** Whatever host
   serves the index serves this tree, so a file written here is readable by anyone who can
   reach the index.

``target_dir``
   Operator-side output — the value of ``--target-out``, which defaults to the working
   directory. Nothing here is published; nothing here is copied into ``out_dir``.

The rule follows directly: an artifact the *host* consumes at deploy time belongs in
``out_dir``; an artifact a *person* consumes belongs in ``target_dir``. A server
configuration, a deploy script, an ``htpasswd`` file, anything holding a credential — all
``target_dir``. This is why the ``nginx`` target does not write next to the index: an nginx
snippet under ``out_dir`` would be served to every client that asked for it, and it names the
filesystem path the server roots at.

Because ``--target-out`` defaults to the working directory, an artifact written there lands
wherever the build was run. That is deliberate — it is in front of the operator rather than
buried — and it is why ``emit`` returns its paths: the command line echoes one
``wrote <path> for the <target> target`` line per artifact, so nothing appears silently.

Built-in targets
================

.. list-table::
   :header-rows: 1
   :widths: 16 30 54

   * - Name
     - Writes
     - Contents
   * - ``static``
     - nothing
     - The default. Emits no files and reports none.
   * - ``cloudflare``
     - ``<out>/_headers``, or the redirector
     - Cloudflare Pages cache rules, read at deploy time — **or**, under
       ``assets: redirect``, ``<out>/_worker.js`` plus ``wrangler.toml`` and ``SETUP.md``
       in ``<target-out>``, and no ``_headers`` at all.
   * - ``nginx``
     - ``<target-out>/ghr-pypi.conf``, plus the allow-list
     - A server-block snippet to ``include``; not a complete ``nginx.conf``. Under
       ``assets: redirect`` it gains an ``/_assets/`` location and is joined by
       ``<target-out>/ghr-pypi-assets.conf``, included at **http** level.

``cloudflare``
--------------

What this target writes depends on :ref:`config-assets`, and the two shapes are alternatives
rather than layers.

Under ``assets: link`` and ``assets: mirror`` it writes ``_headers`` into ``out_dir``, because
Cloudflare Pages reads it from the deployed tree — this one *is* a published artifact,
consumed by the host itself.

``/simple/*`` is given a short ``max-age`` with ``must-revalidate``: the index changes on
every release. Under ``assets: mirror`` two more rules are added — ``/files/*`` is
``immutable`` with a one-year ``max-age``, because a mirrored file never changes under a
given filename, and ``/files/*.metadata`` is pinned to ``application/octet-stream``, which
Pages would otherwise guess wrong for an extension it does not know. The ``.metadata`` rule
carries no ``Cache-Control`` of its own on purpose: Pages joins the headers of every matching
rule, so it inherits immutability from ``/files/*`` above it, and a second copy could only
drift out of step with the first.

Under ``assets: link`` neither ``/files/`` rule is written, because there is no ``files/``
directory to describe.

Under ``assets: redirect`` it writes the redirector **instead**, and **no** ``_headers`` file
at all:

``<out>/_worker.js``
   The redirector, in Pages' *advanced mode*. Pages reads it from the build output root and
   never serves it. Every request reaches it first, so its Basic auth gates the whole index,
   and it fetches its allow-list from the deployed ``_assets/manifest.json`` rather than
   carrying build data — a new release needs a Pages deploy, never a Worker redeploy.

``<target-out>/wrangler.toml``
   The deploy manifest. Operator-side (see :ref:`targets-out-dir`), and **regenerated on
   every build**: it hardcodes ``name = "ghr-pypi"``, so if your Pages project is called
   something else, the rename has to be re-applied after each build — or the file kept
   outside ``--target-out``.

``<target-out>/SETUP.md``
   The deploy steps and the secrets they need. Operator-side because a page describing how
   the index is gated must not be published.

``_headers`` is absent by design, not by omission. Cloudflare documents that "custom headers
defined in the ``_headers`` file are not applied to responses generated by Pages Functions,
even if the request URL matches a rule defined in ``_headers``" — and in advanced mode every
response, index pages included, is generated by ``_worker.js``. A ``_headers`` file here would
be inert *and* misleading: it states a ``public`` cache policy, and this mode puts the whole
site behind Basic auth, so a reader would take away both a rule that is not in force and one
that must not be. The Worker sets the caching and the ``.metadata`` content type itself, on
the responses it delegates to the assets binding, marking every delegated 200 ``private``.
Non-200s are passed through untouched, so a delegated 404 or 304 carries no ``Cache-Control``
at all — an immutable year on a miss would outlive the miss.

``nginx``
---------

Writes ``ghr-pypi.conf`` into ``target_dir`` — see :ref:`targets-out-dir` for why not beside
the index. The snippet is meant to be ``include``\ d inside a ``server`` block; it sets
``root`` to the resolved absolute path of ``out_dir``, turns ``autoindex`` off, and adds
three settings the index actually depends on:

``absolute_redirect off``
   nginx's default directory redirect is absolute and bakes in the ``listen`` port of the
   server block that issued it. Behind a TLS-terminating proxy, or on a non-standard internal
   port, that address is unreachable from outside. A relative ``Location`` lets the client
   keep resolving against the port it connected on.

``default_type application/octet-stream``
   ``.metadata`` has no ``mime.types`` entry, so nginx would serve it as ``text/plain``.
   Setting the fallback fixes that without a ``types { }`` block, which in this context would
   *replace* the inherited map and break every ``.html`` and ``.json``.

``try_files $uri $uri/ =404``
   ``$uri/`` rather than a literal ``index.html`` lets a directory request reach the index
   module, which redirects a bare ``/simple`` to ``/simple/`` before serving it — and that
   redirect is what makes the relative hrefs in the generated pages resolve.

The snippet stops there. It sets **no** caching directives — no ``expires``, no
``Cache-Control`` — and does not configure ``Accept``-header content negotiation for
:pep:`691`. Both are worth having and both are yours to add, in the ``server`` block that
``include``\ s this file: :ref:`howto-json-api` has the negotiation ``map`` and locations, and
:ref:`tutorial-nginx` builds the server block they go in.

Under ``assets: redirect``
~~~~~~~~~~~~~~~~~~~~~~~~~~

Everything above still applies — the site is still served from ``root`` — and two things are
added. The redirector is stock nginx: ``map``, ``auth_basic``, ``try_files`` and
``proxy_pass`` are all compiled in by default, so there is no njs, no ``auth_request``, and no
module to install.

``<target-out>/ghr-pypi.conf``
   Gains ``location ^~ /_assets/``, which authenticates with ``auth_basic``, serves anything
   that exists on disk, and hands everything else to a named location that adds the
   operator's token and proxies to GitHub's asset API. GitHub's 302 to a signed URL is
   returned to the client rather than followed, so no package bytes pass through the server.

``<target-out>/ghr-pypi-assets.conf``
   The allow-list, as a generated ``map`` from published ``_assets/`` URI to GitHub asset API
   path. **Regenerated on every build, and nginx must be reloaded to see it** —
   ``nginx -s reload``. The map is compiled at configuration load, so a running server keeps
   serving the previous one. This is where the two redirectors differ: Cloudflare picks a new
   release up when the site is redeployed, whereas here the index page lists a package whose
   downloads 404 until the reload.

There are two files because ``map`` is valid only in ``http`` context and the rest of the
snippet is ``server`` context — one file cannot be ``include``\ d at both. So
``ghr-pypi-assets.conf`` goes inside ``http { }`` and ``ghr-pypi.conf`` inside the
``server { }`` beneath it. Both are operator artifacts in ``target_dir``; neither may be
published (see :ref:`targets-out-dir`).

Three properties of the generated output are worth stating outright, because each is a
deliberate constraint on what this target will write:

**Neither file contains a credential.** The GitHub token and the Basic auth passwords are
reached by ``include`` and ``auth_basic_user_file``, naming two paths the build does *not*
write. Cloudflare's token is a Pages secret bound by the operator; nginx has no such vault, and
this output lands in a build directory and plausibly a git repository — so a build tool that
put a token in its own output would put it wherever that output goes.

**The** ``auth_basic`` **realm is a fixed literal**, not :ref:`config-title`. The realm is a
quoted nginx string, and interpolating user configuration into one is a config injection.

**The map is what keeps the location from being an open proxy.** A request for a URI it does
not list is a 404. Without it, anyone past ``auth_basic`` could aim the token at any asset it
can read — including repositories this index never listed, since a token's reach is wider than
a build's.

The ``^~`` is load-bearing and is not decoration. A plain prefix location loses to *any*
matching regex location in the same ``server`` block, so one ordinary operator rule —
``location ~ \.whl$ { }`` to add cache headers is the usual shape — would take every asset
request into a block with no ``auth_basic`` in it and serve private files unauthenticated.
``^~`` tells nginx to stop considering regex locations once this prefix is the longest match.
``tests/test_nginx_live.py`` puts exactly that hostile neighbour in the ``server`` block of
every test it runs.

The emitted files carry their own setup instructions as comments — the two paths to create,
the ``htpasswd`` invocation and its bcrypt caveat, which ``resolver`` to use, and where the CA
bundle lives on distributions other than Debian. They are the ``SETUP.md`` of this target, and
they are regenerated with the config, so read them there. :ref:`howto-private-without-mirroring`
covers the deployment and the failure modes.

.. _targets-entry-point:

Registering a target
====================

Targets are discovered through the ``ghr_pypi.targets`` entry point group. The entry point's
name is the name used in ``target:`` and ``--target``; its value must be **a callable that
returns a** :class:`~ghr_pypi.targets.Target` — a class is the usual choice, since calling it
constructs an instance:

.. code-block:: toml

   [project.entry-points."ghr_pypi.targets"]
   fastly = "your_package.targets:FastlyTarget"

Discovery happens on every ``index`` run, over every installed distribution. Two rules keep
that from being a hazard:

* **A built-in wins a name collision.** A plugin registering ``nginx`` is ignored with a
  warning on stderr naming both the entry point and its value. An installed dependency must
  never silently change what a build emits.
* **A plugin that raises while loading is skipped, not propagated.** A stale entry point, a
  value that is not importable, a value that is not callable — each produces the same warning
  and the build continues. An unreferenced plugin, possibly installed transitively, must not
  be able to fail a build that only ever asked for a built-in target.

Both warnings name the entry point *and* its value, because a transitively installed plugin
is otherwise very hard to trace back to the distribution that brought it in.

.. code-block:: text

   warning: ignoring plugin target 'nginx' from your_pkg:NginxTarget: that name is built in

For a worked example, see :ref:`howto-write-a-target`.

.. _targets-hazards:

Two hazards worth knowing
=========================

``emit`` must return a materialized sequence
--------------------------------------------

The return type is :class:`~collections.abc.Sequence`, deliberately not
:class:`~collections.abc.Iterable`. Every write must have happened by the time ``emit``
returns.

A generator that yielded each path as it wrote it would satisfy ``Iterable`` but not
``Sequence``, and a type checker run over the plugin rejects it — which is where the mistake
should be caught, because a generator moves the moment a write can fail out of the target and
into whoever consumes the result.

``ghr-pypi``'s own command line is defensive about it regardless. It materializes whatever a
target returns *inside* the error handling around the ``emit`` call, so a generator that
slipped past type checking still fails cleanly as
``error: <target> target failed: <reason>``, exit 1, with no traceback and no success line.
It also rejects a return value that is not a sequence at all — ``None``, the shape you get by
forgetting the ``return`` — with
``error: <target> target returned <type>, expected a sequence of paths``.

Both of those are this caller being careful, not a guarantee of the protocol. Another consumer
of a target need not be, which is why the contract is a materialized sequence rather than
"whatever the CLI happens to survive". Write your paths to a list and return it.

``isinstance`` against the protocol proves almost nothing
---------------------------------------------------------

:class:`~ghr_pypi.targets.Target` is ``@runtime_checkable``, which makes
``isinstance(obj, Target)`` legal. It is also nearly meaningless: a runtime-checkable protocol
checks that the named attributes **exist**, not their signatures and not their return types.
An object whose ``emit`` takes no arguments, or returns ``None``, passes.

Treat a passing ``isinstance`` as "has the right attribute names" and nothing more.

Reaching for a type checker is not enough on its own either. Nothing structurally ties a
plugin to ``Target``: a class is only checked against a protocol where it is *assigned* to
one, and a plugin is normally only ever named in an entry point, which no checker reads. A
class with ``def emit(self) -> None`` — wrong parameters and wrong return — passes
``isinstance`` **and** passes both mypy and pyright.

What closes the gap is one explicit conformance assertion, next to the class:

.. code-block:: python

   _check: Target = YourTarget()   # type-checked conformance; isinstance cannot do this

That assignment is the point where the checker compares your class against the protocol. With
it, both mypy and pyright reject the shape above and name the mismatch. mypy puts it this way:

.. code-block:: text

   Expected:
       def emit(self, site: SiteContext) -> Sequence[Path]
   Got:
       def emit(self) -> None

pyright reports the same incompatibility in its own wording. Without the assertion, neither
has anything to compare and both report success.

See also
========

* :ref:`config-target` — the configuration key, and why the name is not validated there.
* :ref:`config-assets` — the other axis: where the index's links point.
* :ref:`cli` — ``--target``, ``--target-out``, and every target-related exit-1 message.
* :ref:`howto-write-a-target` — a target written, registered and run end to end.
* :ref:`howto-private-without-mirroring` — both redirectors, deployed.
