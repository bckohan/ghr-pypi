import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import ghr_pypi

project = ghr_pypi.__title__
copyright = ghr_pypi.__copyright__
author = ghr_pypi.__author__
release = os.environ.get("PACKAGE_VERSION") or ghr_pypi.__version__

extensions = [
    "sphinx.ext.intersphinx",
    "sphinx.ext.autodoc",
    "sphinx.ext.todo",
    "sphinx_tabs.tabs",
    "sphinx.ext.viewcode",
    "myst_parser",
    "sphinxcontrib.typer",
]

templates_path = ["_templates"]
exclude_patterns = []

# index.rst includes the README body as a fragment: its top level headings are ``##``
# because the ``#`` title and the badges stay behind on GitHub. MyST cannot know that
# and flags every one of them, so silence just that check.
suppress_warnings = ["myst.header"]

html_theme = "furo"
html_theme_options = {
    "source_repository": "https://github.com/bckohan/ghr-pypi/",
    "source_branch": "main",
    "source_directory": "doc/source",
}

html_static_path = ["_static"]

todo_include_todos = True

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

linkcheck_allow_redirects = True
# Medium serves 403 to automated clients; the link is fine in a browser.
linkcheck_ignore = [r"https://medium\.com/.*"]


def pypi_role(name, rawtext, text, lineno, inliner, options={}, content=[]):
    from docutils import nodes

    url = f"https://pypi.org/project/{text}/"
    node = nodes.reference(rawtext, text, refuri=url, **options)
    return [node], []


def setup(app):
    from docutils.parsers.rst import roles
    roles.register_local_role("pypi", pypi_role)
