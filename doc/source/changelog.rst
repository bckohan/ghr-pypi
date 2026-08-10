.. include:: ./refs.rst

Changelog
=========

2026.8.X
--------

* Do not stop at first page of releases.
* Allow wild card specifiers for repositories within orgs in the config file.
* Deployment targets: ``target: cloudflare`` writes Cloudflare Pages cache
  headers and ``target: nginx`` writes a server snippet, through a documented
  ``ghr_pypi.targets`` plugin interface.
* ``assets: redirect``: serve private repositories without mirroring. Every
  index link points at the site's own ``_assets/`` paths, the build emits an
  allow-list of the published assets beside them, and the ``cloudflare`` target
  ships a token-holding Cloudflare Worker that authenticates the whole site and
  redirects each asset request to GitHub's short-lived signed URL. Targets opt
  in with a ``supports_redirect`` attribute; any target that does not is
  refused the mode.
* The ``nginx`` target now supports ``assets: redirect`` too, serving private
  release assets on **stock nginx** with no additional modules — no njs and no
  ``auth_request``. It emits an ``/_assets/`` location gated by ``auth_basic``
  that proxies to GitHub's asset API and returns the signed 302 unfollowed,
  plus ``ghr-pypi-assets.conf`` — the allow-list as a generated ``map``,
  ``include``\ d at ``http`` level because ``map`` is valid nowhere else.
  Neither file contains the token or a password; both are ``include``\ d from
  paths the build deliberately does not write.
* New ``missing_metadata`` key (``extract`` | ``warn``, default ``extract``),
  accepted only under ``assets: redirect``: it decides whether a wheel whose
  release carries no ``.metadata`` asset is downloaded once so the sidecar can
  be written into the site — :pep:`658` metadata for a private repository
  without mirroring its wheels.
* Added ``ghr-pypi webhook --index-repo OWNER/NAME [--out DIRECTORY]``, which
  writes a standalone Cloudflare Worker (plus ``wrangler.toml`` and a
  ``SETUP.md`` checklist). Pointed at a GitHub organization webhook, it
  validates each delivery's HMAC signature and turns a release published in
  *any* repository the hook covers into a ``repository_dispatch`` that rebuilds
  the index. Independent of ``target``.
* The Pages workflow now also triggers on ``repository_dispatch`` of type
  ``ghr-pypi-rebuild``, which is what the receiver — or a releasing
  repository's own workflow — sends. See "How do I rebuild when another
  repository releases?" for all three ways to keep an aggregating index fresh.
* The tutorials now assume a repository whose release process already attaches
  wheels to its Releases, and none of them writes a package for you. There are
  four: GitHub Pages, indexing other repositories, and a private index under
  ``assets: redirect`` on each of the two redirectors — a Cloudflare Worker and
  stock nginx. The nginx one replaces the static-site walkthrough; content
  negotiation moved to "How do I serve the PEP 691 JSON API?".
* The GitHub token is now named ``GHR_PYPI_TOKEN`` everywhere it appears: the
  CLI reads that environment variable first (``GITHUB_TOKEN`` still works as a
  fallback, so workflows passing the built-in ``github.token`` are unaffected),
  and the Cloudflare redirector and webhook receiver read it as their secret
  name — a **breaking** rename for existing deployments, which must re-bind the
  secret under the new name on their next deploy. The old name was overloaded:
  exporting ``GITHUB_TOKEN`` in a shell hijacks ``gh`` and VSCode git auth, and
  a GitHub Actions secret cannot even be called ``GITHUB_TOKEN``.
* The Cloudflare tutorial is now turnkey: it starts from the
  `cloudflare-pypi-template <https://github.com/bckohan/cloudflare-pypi-template>`_
  repository, whose single idempotent workflow creates the Pages project, binds
  the Worker's secrets, builds, and deploys — entirely from GitHub Actions,
  with nothing run locally. The index rebuilds daily by default; the tutorial
  closes with per-repository ``repository_dispatch`` for instant rebuilds, and
  the organization webhook receiver moved to
  "How do I rebuild when another repository releases?".
* Raised the minimum ``typer`` to 0.18. click 8.3 moved required-option
  enforcement, and typer 0.16/0.17 paired with it stop reporting a missing
  required option at all, passing ``None`` into the command instead —
  ``ghr-pypi webhook`` with no ``--index-repo`` exited 1 complaining about a
  repository named ``None`` rather than 2 with a usage message.
* **Deprecated:** the ``mirror`` key is now ``assets: link | mirror``.
  ``mirror: true`` still works and warns.

2026.8.8
--------

* Support yank/exclude.
* ``repositories`` entries may use an ``fnmatch`` pattern in the name half —
  ``yourorg/*`` indexes every repository the token can read in that owner — with
  a new ``exclude_repositories`` key to subtract from expansions.
* Every GitHub list endpoint is now paginated: repositories with more than 100
  releases are read in full.
* The positional ``REPO`` argument takes zero or more repositories and defaults
  to ``$GITHUB_REPOSITORY``; ``--out`` defaults to ``_site`` and the config
  file's ``repositories`` key is optional. A GitHub Pages workflow can now run
  ``ghr-pypi index`` with no arguments.
* **Breaking:** the index build is now the ``index`` subcommand —
  ``ghr-pypi OWNER/NAME`` becomes ``ghr-pypi index OWNER/NAME``. Bare
  ``ghr-pypi`` prints help.
* Added ``ghr-pypi extract-meta PATH...``, which writes each wheel's :pep:`658`
  core metadata to ``<wheel>.metadata`` for upload as a release asset.


2026.8.6
--------

* Initial release.
* Aggregate releases from multiple repositories via ``--config`` (YAML).
* Template override hooks: a config-specified directory and ``builtin/``-prefixed block inheritance.
* Use GitHub's asset digests instead of downloading to hash; ``missing_digest``
  config policy for digest-less assets.
* Emit a PEP 691/700 JSON Simple API alongside the HTML index, controlled by
  the ``formats`` config key.
* ``mirror`` mode: download assets into the site (private-repo support,
  self-contained output, incremental re-builds).
* Serve PEP 658/714 core metadata: extracted from mirrored wheels, passed
  through from ``.metadata`` release assets, with per-repository coverage
  warnings.
* Release workflow publishes each wheel's PEP 658 ``.metadata`` as a
  release asset.
* ``yanked`` and ``exclude`` config keys, keyed by project then version:
  ``yanked`` marks files PEP 592 yanked (``data-yanked`` in the HTML, a
  ``yanked`` key in the JSON) while leaving them installable by exact pin;
  ``exclude`` keeps them out of the index entirely.
* Versions are computed at build time from git tags; no version strings are
  stored in the repository.
* Added a full Diátaxis documentation manual: tutorials, how-to guides, and
  reference.
