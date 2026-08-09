"""The default target: a plain static host needs no artifacts."""

from collections.abc import Sequence
from pathlib import Path

from ghr_pypi.targets import SiteContext


class StaticTarget:
    """Emits nothing.

    It exists so that "no deployment artifacts" is a named choice rather than
    an absence, and so the CLI always has a target to call.
    """

    name = "static"

    def emit(self, site: SiteContext) -> Sequence[Path]:
        """Write nothing at all, and report nothing written."""
        return ()
