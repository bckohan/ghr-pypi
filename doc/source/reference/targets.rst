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
     - ``<out>/_headers``
     - Cloudflare Pages cache rules, read at deploy time.
   * - ``nginx``
     - ``<target-out>/ghr-pypi.conf``
     - A server-block snippet to ``include``; not a complete ``nginx.conf``.

``cloudflare``
--------------

Writes ``_headers`` into ``out_dir``, because Cloudflare Pages reads it from the deployed
tree — this one *is* a published artifact, consumed by the host itself.

``/simple/*`` is given a short ``max-age`` with ``must-revalidate``: the index changes on
every release. Under ``assets: mirror`` two more rules are added — ``/files/*`` is
``immutable`` with a one-year ``max-age``, because a mirrored file never changes under a
given filename, and ``/files/*.metadata`` is pinned to ``application/octet-stream``, which
Pages would otherwise guess wrong for an extension it does not know. The ``.metadata`` rule
carries no ``Cache-Control`` of its own on purpose: Pages merges the headers of every matching
rule, so it inherits immutability from ``/files/*`` above it, and a second copy could only
drift out of step with the first.

Under ``assets: link`` neither ``/files/`` rule is written, because there is no ``files/``
directory to describe.

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
:pep:`691`. Both are worth having and both are yours to add; :ref:`tutorial-nginx` writes a
configuration by hand that includes them.

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
