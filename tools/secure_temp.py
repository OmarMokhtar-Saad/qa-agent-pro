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
import stat
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Subdirectory name under the system temp dir where exports live.
SUBDIR_NAME = "qa_agents_exports"


def _is_ours(path: Path) -> bool:
    """True for a real (non-symlink) directory owned by the current user."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid()


def secure_temp_dir() -> Path:
    """Return the per-app 0700 export directory, creating/tightening it as needed.

    The shared temp root is world-writable, so another user (or a symlink) can
    pre-create ``SUBDIR_NAME``. A pre-existing dir that is not a real directory
    owned by us is NOT used; a per-uid sibling is used instead, and if that too
    is foreign the call refuses rather than writing exports into it.
    """
    root = Path(tempfile.gettempdir())
    for name in (SUBDIR_NAME, f"{SUBDIR_NAME}-{os.getuid()}"):
        base = root / name
        try:
            base.mkdir(mode=0o700, exist_ok=True)
        except OSError:
            # Exists as a file/symlink we cannot use; fall through to ownership check.
            pass
        if not _is_ours(base):
            logger.warning(
                "Export dir %s is not a directory owned by us; skipping", base
            )
            continue
        try:
            # mkdir's mode is masked by umask and a no-op if the dir pre-existed, so
            # explicitly tighten to owner-only every time.
            os.chmod(base, 0o700)
        except OSError:
            logger.warning("Could not set 0700 permissions on export dir %s", base)
        return base
    raise PermissionError(
        f"no safe export directory available under {root} "
        f"({SUBDIR_NAME} is not owned by the current user)"
    )


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


def _delete_if_stale(
    path: Path, now: float, max_age_seconds: int, label: str, log: logging.Logger
) -> bool:
    """Unlink *path* when it is our regular file older than the limit; may raise OSError."""
    # Only files THIS tool created: a regular file (never a symlink)
    # owned by us. The legacy tmp-root sweep shares a directory with
    # every other program, so the name pattern alone is not enough.
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
        return False
    age = now - st.st_mtime
    if age <= max_age_seconds:
        return False
    path.unlink(missing_ok=True)
    log.info(
        "Cleaned up stale %s temp file: %s (age %.0fs)",
        label,
        path,
        age,
    )
    return True


def cleanup_temp_files(
    *,
    max_age_seconds: int,
    pattern: str,
    label: str,
    log: logging.Logger,
    sweep_tmp_root: bool = False,
    require_subdir: bool = True,
    skip_suffix: str = "",
) -> int:
    """Delete stale export files matching *pattern* older than *max_age_seconds*.

    Sweeps ``<tmp>/SUBDIR_NAME`` (the secure per-app dir); with *sweep_tmp_root*
    also the tmp root (legacy pre-QW-18 files). *require_subdir* returns 0 when the
    subdir is missing. *skip_suffix* skips names ending with it. Age test is strict
    (``age > max``); a per-file OSError is logged and skipped. Returns the count.
    ``tempfile.gettempdir()`` is read at CALL time so tests that patch it work.
    """
    tmp_dir = Path(tempfile.gettempdir())
    subdir = tmp_dir / SUBDIR_NAME
    if require_subdir and not subdir.is_dir():
        return 0
    roots = (subdir, tmp_dir) if sweep_tmp_root else (subdir,)
    now = time.time()
    deleted = 0
    for base in roots:
        for path in base.glob(pattern):
            if skip_suffix and path.name.endswith(skip_suffix):
                continue
            try:
                if _delete_if_stale(path, now, max_age_seconds, label, log):
                    deleted += 1
            except OSError:
                log.warning("Could not check/delete temp file: %s", path)
    if deleted:
        log.info("%s cleanup: removed %d stale file(s)", label, deleted)
    return deleted
