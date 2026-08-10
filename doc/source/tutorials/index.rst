.. include:: ../refs.rst

.. _tutorials:

=========
Tutorials
=========

These are lessons, not recipes. Each one ends with a package you install from an index you built
yourself, and each is self-contained: follow any single tutorial start to finish, in order,
without reading the others. There is nothing to design along the way, and everything you need to
type is on the page.

All four start in the same place: **a GitHub repository whose release process already attaches
wheels to its Releases**. None of them sets that up — each begins where ``ghr-pypi`` begins.
What they ask for beyond it differs. :ref:`tutorial-cloudflare` wants a Cloudflare account and
starts from a template repository, so it is browser-only;
:ref:`tutorial-nginx` wants a server you can ``sudo`` on,
a DNS name pointing at it, and a TLS certificate for that name, which it does not obtain for
you. :ref:`tutorial-other-repositories` wants **two or more** source repositories and a separate
one to host the index. :ref:`tutorial-github-pages` is the only one that needs nothing but the
repository it starts from. Each tutorial's *What you will need* is the authority on its own.

Come back to the :ref:`how-to guides <how-to>` and the :ref:`reference <reference>` once you
want to adapt what you built.

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   github-pages
   other-repositories
   cloudflare
   nginx

Which one should I do?
======================

Any of them, and the first one is the shortest. They differ in what goes into the index, in
where it ends up, and in what that host is able to do for you.

.. list-table::
   :header-rows: 1
   :widths: 22 14 64

   * - Tutorial
     - Time
     - What it teaches
   * - :ref:`tutorial-github-pages`
     - ~10 min
     - Zero infrastructure and zero configuration. One GitHub Actions workflow builds the
       index and deploys it; the packages stay on GitHub and are linked to.
   * - :ref:`tutorial-other-repositories`
     - ~15 min
     - A configuration file, and the shift that comes with it: the index stops describing the
       repository it lives in and covers other repositories instead, listed or matched by
       pattern.
   * - :ref:`tutorial-cloudflare`
     - ~15 min
     - A private index, with nothing copied anywhere: the packages stay on GitHub and a
       Worker at the edge holds the token, checks a password on every request, and hands
       back a signed URL. Built from a template repository, deployed entirely from GitHub
       Actions — nothing runs on your machine.
   * - :ref:`tutorial-nginx`
     - ~25 min
     - The same private index on a server you own, served by stock nginx with no extra
       modules: nginx holds the token, gates the downloads, and returns GitHub's signed URL.
       Then when to mirror instead.

If you have no preference, do :ref:`tutorial-github-pages`. It is the fastest route to a
working index, and the other three vary it.
