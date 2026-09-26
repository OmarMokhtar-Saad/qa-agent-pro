"""Server-side staging for content too sensitive for the shared system temp dir.

Anything a handler needs to write to disk as an intermediate artifact (staged
Jira ticket parts, capture ledgers, etc.) belongs here rather than under
``tempfile.gettempdir()``: that directory is world-readable on a shared
machine, and a per-app 0700 subdirectory of it is still discoverable by name
even though its contents are not (see ``tools/secure_temp.py``, which is
scoped to *export* artifacts and deliberately stays under the system temp
dir). This module keeps every staged artifact under this install's own
app-data root instead (``~/.qa-agents/staging`` by default), matching the
layout ``tools/mobile/paths.py`` already uses for the mobile lane cache: one
root a tester can delete to reset everything.

Pruning reuses the exact shape every exporter's ``cleanup_temp_files``
already has (glob the directory, compare ``st_mtime`` age, unlink past the
threshold) rather than inventing a second, divergent pruning design.

House rules obeyed here: no ``print``. Every public function either returns a
plain value from pure path arithmetic (nothing to fail at) or degrades to a
safe default (an empty count, a created-on-demand directory) rather than
raising, the same never-raises posture ``tools/mobile/paths.py`` documents
for its own cache root.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

#: Default root, relative to the user's home directory. Overridable with
#: QA_STAGING_DIR for the same reason tools/mobile/paths.py's cache root is
#: overridable: a test or an operator needs to redirect it without touching
#: $HOME.
DEFAULT_ROOT_PARTS: tuple[str, ...] = (".qa-agents", "staging")

#: Spelled out at the read site below rather than held here: selfcheck's
#: classify_live_env justifies a LIVE_ENV name by finding it NEXT TO an
#: environment read, and an indirection hides it from that guard.
_ENV_VAR = "QA_STAGING_DIR"


def staging_root() -> Path:
    """The staging root. Pure: creates nothing, never raises."""
    raw = (os.environ.get("QA_STAGING_DIR") or "").strip()
    if raw:
        try:
            return Path(raw).expanduser()
        except Exception:
            logger.warning(
                "staging: unusable %s=%r -- using the default", _ENV_VAR, raw
            )
    return Path.home().joinpath(*DEFAULT_ROOT_PARTS)


def staging_dir(name: str) -> Path:
    """Create (0700) and return a named subdirectory of the staging root.

    Never under ``tempfile.gettempdir()`` -- that is the one thing this
    helper exists to avoid; see the module docstring.
    """
    directory = staging_root() / str(name)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        # mkdir's mode is masked by umask and a no-op if the dir pre-existed,
        # so tighten explicitly every call -- see tools/secure_temp.py.
        os.chmod(directory, 0o700)
    except OSError:
        logger.warning("Could not set 0700 permissions on staging dir %s", directory)
    return directory


def make_staging_path(name: str, prefix: str, suffix: str) -> str:
    """Create an empty 0600 file under ``staging_dir(name)``; return its path.

    ``tempfile.mkstemp`` creates the file atomically with 0600 permissions,
    closing the race window a create-then-chmod sequence leaves open -- the
    same reasoning ``tools/secure_temp.make_secure_temp_path`` uses.
    """
    fd, path = tempfile.mkstemp(
        prefix=prefix, suffix=suffix, dir=str(staging_dir(name))
    )
    os.close(fd)
    return path


def prune_stale(name: str, glob_pattern: str, max_age_seconds: int = 3600) -> int:
    """Delete files under ``staging_dir(name)`` matching glob_pattern that are
    older than max_age_seconds. Returns the count deleted; never raises.

    Same age-based sweep every exporter's ``cleanup_temp_files`` already runs
    (see tools/xlsx_generator.py) -- reused rather than reinvented, so this
    staging root gets the one pruning shape the codebase already has instead
    of a second, divergent one.
    """
    deleted = 0
    try:
        base = staging_dir(name)
        now = time.time()
        for path in base.glob(glob_pattern):
            try:
                age = now - path.stat().st_mtime
                if age > max_age_seconds:
                    path.unlink(missing_ok=True)
                    deleted += 1
                    logger.info(
                        "Cleaned up stale staged file: %s (age %.0fs)", path, age
                    )
            except OSError:
                logger.warning("Could not check/delete staged file: %s", path)
    except Exception:
        logger.debug("prune_stale(%s) failed", name, exc_info=True)
    return deleted
