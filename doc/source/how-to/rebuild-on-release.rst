.. include:: ../refs.rst

.. _howto-rebuild-on-release:

==================================================
How do I rebuild when another repository releases?
==================================================

An index rebuilds when something tells it to. Publishing a release in the
repository that *hosts* the index fires a ``release`` event there, and its Pages
workflow runs — unless that release was created by a workflow using the built-in
``GITHUB_TOKEN``, whose events GitHub suppresses so that workflows cannot
trigger themselves. Publishing a release in any **other** repository fires
nothing in the index repository at all — GitHub delivers events to the
repository they happened in, and nowhere else.

So an aggregating index — anything built from a fixed list of other
repositories, or from a ``yourorg/*`` pattern — goes stale silently. Nothing
fails; the index simply keeps describing yesterday's releases. This guide is the
three ways to close that gap.

Pick the row you are on
=======================

.. list-table::
   :header-rows: 1
   :widths: 30 45 25

   * - Situation
     - What closes the gap
     - What it is
   * - You control the releasing repositories, but do not own the organization
     - Their release workflow calls the dispatch API
     - Configuration
   * - You own the organization that holds them
     - One organization webhook → a receiver → the dispatch API
     - Software to deploy
   * - You control neither
     - A scheduled rebuild
     - Configuration

**Permissions decide which row you are on, not preference.** GitHub's webhook
documentation states it in two sentences, and they are not the same sentence:

   You must be a repository owner or have admin access in the repository to
   create and manage webhooks in a repository.

   You must be an organization owner to create and manage webhooks in an
   organization.

— `Types of webhooks <https://docs.github.com/en/webhooks/types-of-webhooks>`_
and `Creating webhooks
<https://docs.github.com/en/webhooks/using-webhooks/creating-webhooks>`_.

Organization **owner** is a strictly higher bar than admin on a repository:
being an admin of some of an organization's repositories, or even all of them,
does not grant it. One organization webhook covers every repository the
organization owns, including ones created later — that coverage is the whole
reason row two is worth deploying software for. The rows are written so that
exactly one of them describes you; owning the organization puts you in row two
even where you could also do row one.

The receiver accepts a per-repository webhook just as happily, but that is not a
fourth route worth taking. A repository webhook is created on one repository at a
time, which is the same per-repository work row one already asks for, with a
Worker to run on top of it. **If you administer the repositories but do not own
the organization, you are in row one.**

Neither is available for a repository belonging to someone else, at any
permission level. **Row three is the only answer for third-party
repositories**, and no amount of configuration changes that.

Rows one and two both end in the same API call, so both need the index
repository listening for it. Its Pages workflow gains:

.. code-block:: yaml

   on:
     repository_dispatch:
       types: [ghr-pypi-rebuild]

That workflow file has to be on the repository's **default branch**;
``repository_dispatch`` triggers nothing from any other branch. Until the
trigger is there every hop still reports success and nothing rebuilds: GitHub's
dispatch endpoint answers whoever called it ``204 No Content``, and on the
row-two route the receiver in turn answers GitHub ``202``, a green delivery in
the hook's Recent Deliveries. Neither number says anything about whether a
workflow was listening — that is the failure this trigger exists to prevent, and
neither end will report it for you.

Repositories you control
========================

A runner is already executing in the repository that published the release, so
it can make the call itself. Add a step to that repository's release workflow:

.. code-block:: yaml

   - name: Ask the package index to rebuild
     env:
       GH_TOKEN: ${{ secrets.INDEX_DISPATCH_TOKEN }}
     run: |
       gh api repos/yourorg/pypi/dispatches \
         -f event_type=ghr-pypi-rebuild \
         -f "client_payload[repository]=$GITHUB_REPOSITORY"

``yourorg/pypi`` is the repository that *serves* the index, not the one that
just released. ``$GITHUB_REPOSITORY`` is the one that did, and it is read from
the environment rather than interpolated with ``${{ }}`` — the same habit that
keeps any workflow expression out of a shell command.

The ``client_payload`` is not decoration and is not optional if you also use
row two. The receiver sends exactly this field, so sending it here too means a
rebuild workflow can read ``github.event.client_payload.repository`` and get the
same answer whichever route triggered it. ``pages.yml`` reads neither today;
the point is that the day it does, it will not quietly work from one route and
not the other.

**The workflow's built-in** ``GITHUB_TOKEN`` **cannot do this.** It is scoped to
the repository the workflow runs in, and the index lives somewhere else. Use a
fine-grained personal access token or a GitHub App installation token with
**Contents: Read and write** on the index repository — that single permission is
what ``POST /repos/{owner}/{repo}/dispatches`` requires — and store it as a
secret in each releasing repository.

That is also the cost of this row: one secret per releasing repository, renewed
whenever the token expires, and a repository added next year is a repository
that silently is not doing it. Pasting this step into ten repositories is this
row done ten times, not row two.

An organization you own
=======================

A webhook is GitHub POSTing to a URL, and a URL needs something listening. No
GitHub feature is that listener, so this row is the one that needs software
deployed. ``ghr-pypi`` generates it:

.. code-block:: sh

   ghr-pypi webhook --index-repo yourorg/pypi

That writes ``worker.js``, ``wrangler.toml`` and ``SETUP.md`` into ``webhook/``
(or ``--out``). The Worker validates the delivery's ``X-Hub-Signature-256``
HMAC, ignores everything that is not a ``release`` event, and calls the same
dispatch endpoint the snippet above does. It is a standalone Cloudflare Worker,
independent of ``--target`` — an index on plain GitHub Pages needs it exactly as
much as one on Cloudflare.

**Follow the generated** ``SETUP.md``, not this page, for the procedure. It is
regenerated for your index on every run, with your repository already
substituted in, so it cannot drift out of step with the Worker beside it. In
outline it is: ``wrangler deploy``, then ``wrangler secret put WEBHOOK_SECRET``
and ``wrangler secret put GITHUB_TOKEN``, then create the hook in the
organization's **Settings → Webhooks**:

* **Payload URL** — what the deploy printed.
* **Content type** — ``application/json``. The receiver verifies the HMAC over
  the raw request body and then parses those same bytes as JSON. GitHub's other
  choice wraps the payload in a form field, which parses as nothing and answers
  ``400 Malformed payload`` on every release.
* **Secret** — the same value you bound as ``WEBHOOK_SECRET``. A mismatch is a
  ``401`` on every delivery, visible in the hook's Recent Deliveries.
* **Which events** — "Let me select individual events", then **Releases** only.
  Everything else is checked and dropped anyway, so sending more only burns
  Worker invocations.

``GITHUB_TOKEN`` here is the same fine-grained or App token row one needs, with
**Contents: Read and write** on the index repository — but you bind it *once*,
to the Worker, instead of once per releasing repository. That, and covering
repositories that do not exist yet, is what you are buying.

Repositories you control neither
================================

If you cannot create a webhook on it and cannot edit its workflows, nothing it
does can reach you. Poll instead — a ``schedule:`` block in the index
repository's own Pages workflow:

.. code-block:: yaml

   on:
     schedule:
       - cron: "17 6 * * *" # daily, deliberately off the hour

Two honest costs, both worth knowing before you rely on it:

**Staleness is bounded, not eliminated.** A release published a minute after a
run waits the whole interval. Shortening the interval trades API calls and
runner minutes for a tighter bound; it never reaches zero. Scheduled runs are
also queued rather than guaranteed on time, and are delayed under load, so treat
the interval as an upper bound you will sometimes exceed. Times off the hour
avoid the worst of it.

**GitHub turns scheduled workflows off after 60 days of inactivity.** The
`documentation
<https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows>`_
is narrower than that sentence, and the narrowing is worth reading rather than
relying on: "In a public repository, scheduled workflows are automatically
disabled when no repository activity has occurred in 60 days." A public index
repository whose only job is to rebuild from *other* repositories' releases has,
by construction, no activity of its own — so this lands on exactly the
repository that most needs the schedule. GitHub emails the repository's admins,
and re-enabling is a button on the Actions tab, but nothing in the index itself
says it stopped. If the index matters, either commit something periodically or
watch for that notice.

Belt and braces
===============

The rows are not exclusive, and combining them is the usual answer for a real
index: rows one or two for the repositories you can reach, so releases show up
in seconds, and row three underneath as a floor for everything else and as
insurance against a rotated token or a misconfigured hook.

That combination is safe because **a rebuild is idempotent**. Every run reads
the configuration again and rebuilds the index from scratch from whatever the
GitHub API currently reports, so a rebuild triggered for no reason produces the
same site as the previous one. The cost of a spurious rebuild is runner minutes,
not a wrong index.

That is also why the receiver deliberately has **no replay protection and no
repository allow-list**. Both would need state or configuration to maintain, and
they would buy nothing: the worst a replayed delivery or an irrelevant
repository's release can do is cause a rebuild that produces the same site.
Signature verification is still mandatory — it is what stops an unauthenticated
caller from spending your runner minutes at will — but past that point, extra
rebuilds are simply not a failure mode.

A burst of releases does not become a burst of builds either. The Pages workflow
declares ``concurrency: {group: pages, cancel-in-progress: true}``, so several
dispatches arriving together collapse into one run against the newest state,
which is what you want when an organization publishes five packages at once.

Next
====

* :ref:`cli-webhook` — every file the command writes and every way it exits 1.
* :ref:`howto-index-an-organization` — the ``yourorg/*`` pattern, and the token
  that has to be able to read every repository it expands to.
* :ref:`howto-fast-rebuilds` — once rebuilds are frequent, what each one costs.
* :ref:`tutorial-other-repositories` — where this staleness comes from, built step by step.
* :ref:`tutorial-cloudflare` — the organization-webhook route deployed end to end.
