"""Secure temp-file helpers shared by the exporters (QW-18 / I-008 / B-037).

Generated test material can contain sensitive product detail, so exports are
written into a per-app, owner-only (0700) subdirectory of the system temp dir,
as 0600 files created via ``tempfile.mkstemp`` — instead of world-readable files
dropped directly in a shared /tmp. The sweep helpers still clean both the new
subdirectory and the legacy root location so pre-QW-18 files are not orphaned.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Subdirectory name under the system temp dir where exports live.
SUBDIR_NAME = "qa_agents_exports"


def secure_temp_dir() -> Path:
    """Return the per-app 0700 export directory, creating/tightening it as needed."""
    base = Path(tempfile.gettempdir()) / SUBDIR_NAME
    base.mkdir(mode=0o700, exist_ok=True)
    try:
        # mkdir's mode is masked by umask and a no-op if the dir pre-existed, so
        # explicitly tighten to owner-only every time.
        os.chmod(base, 0o700)
    except OSError:
        logger.warning("Could not set 0700 permissions on export dir %s", base)
    return base


def make_secure_temp_path(prefix: str, suffix: str) -> str:
    """Create an empty 0600 temp file in the secure dir; return its path (fd closed).

    mkstemp creates the file atomically with 0600 permissions, closing the
    race window that NamedTemporaryFile-then-reopen leaves.
    """
    fd, path = tempfile.mkstemp(
        prefix=prefix, suffix=suffix, dir=str(secure_temp_dir())
    )
    os.close(fd)
    return path
