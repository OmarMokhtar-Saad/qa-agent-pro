"""Create the mobile cache directory tree (split out of ``paths.ensure_tree``)."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("tools.mobile.paths")


def build_tree(root: Path, names: tuple[str, ...]) -> dict[str, str]:
    """Make *root* (0700 where the OS allows it) and each child; return the child paths."""
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        # Windows ignores POSIX modes and some network volumes refuse the
        # call outright. A cache we cannot tighten is still usable, and
        # nothing credential-shaped is ever written into it.
        logger.info("mobile.paths: could not tighten permissions on %s", root)
    dirs: dict[str, str] = {}
    for name in names:
        child = root / name
        child.mkdir(parents=True, exist_ok=True)
        dirs[name] = str(child)
    return dirs
