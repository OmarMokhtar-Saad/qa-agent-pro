"""The retention sweep of the per-process log files.

Part of the ``tools.log_setup`` facade; see its docstring for the design.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from tools import pid_probe
from tools.log_setup_base import DEFAULT_PREFIX, _pid_from_name
from tools.log_setup_env import (
    RETENTION_DAYS,
    RETENTION_ENABLED,
    RETENTION_MAX_FILES,
)

logger = logging.getLogger("qa_agents.log_setup")

# 2026-08-09 (review L2): off POSIX _pid_is_alive is ALWAYS False, so every
# peer's file looks dead and a startup sweep past RETENTION_MAX_FILES could
# delete a LIVE peer's already-rotated .log.1 / .log.2 -- Windows' open-file
# protection covers only the file the peer currently has open. A backup touched
# inside this window almost certainly belongs to a running process, so it is
# skipped. POSIX is unaffected: there the pid check is authoritative.
_NON_POSIX_GRACE_S = 3600.0


def off_posix() -> bool:
    """Is this a non-POSIX platform? Read through ONE helper (review W5) so a
    test can exercise the Windows branch by patching THIS module, instead of
    monkeypatching the stdlib's ``os.name`` process-wide. Never raises."""
    return os.name != "posix"


# Is a process with this pid running? Unknown counts as ALIVE (keep the file).
#
# WHY THIS EXISTS: on POSIX, unlinking a file another process still has OPEN
# succeeds and silently strands every subsequent line of that peer in an
# unreachable inode -- a miniature recurrence of the very bug this module fixes.
# A peer that has simply been idle longer than the age window is the realistic
# case on a three-client install.
#
# POSIX only, deliberately: on Windows ``os.kill(pid, 0)`` calls TerminateProcess
# and would KILL the peer, so this returns False there and the protection is the
# OS's own -- Windows refuses to unlink an open file, and that failure is already
# swallowed by the guarded unlink in ``sweep_old_logs``.
def _pid_is_alive(pid: int | None) -> bool:
    """Is a process with this pid running? Unknown counts as ALIVE."""
    if pid is None:
        return True
    if off_posix():
        return False
    return pid_probe.pid_is_alive(pid)


class SweepScan(NamedTuple):
    """What ``_sweep_candidates`` scans: a directory, a prefix, the active file."""

    directory: Path
    prefix: str
    active: str
    now_ts: float


def _sweep_candidates(scan: SweepScan) -> list[tuple[float, Path]]:
    """Return (mtime, path) for every file the sweep may delete, newest first."""
    entries: list[tuple[float, Path]] = []
    for path in scan.directory.glob(f"{scan.prefix}-*.log*"):
        try:
            if scan.active and path.name.startswith(scan.active):
                continue
            if _pid_is_alive(_pid_from_name(path.name, scan.prefix)):
                continue
            mtime = path.stat().st_mtime
            # See _NON_POSIX_GRACE_S: off POSIX the pid check cannot help.
            if off_posix() and (scan.now_ts - mtime) < _NON_POSIX_GRACE_S:
                continue
            entries.append((mtime, path))
        except OSError:
            continue
    entries.sort(key=lambda item: item[0], reverse=True)
    return entries


def _delete_stale(
    entries: list[tuple[float, Path]], keep_n: int, cutoff: float
) -> list[str]:
    """Delete entries beyond *keep_n* or older than *cutoff*; return names removed."""
    removed: list[str] = []
    for index, (mtime, path) in enumerate(entries):
        if index < keep_n and mtime >= cutoff:
            continue
        try:
            path.unlink()
            removed.append(path.name)
        except OSError:
            logger.debug("could not delete the old log %s", path, exc_info=True)
    return removed


@dataclass(frozen=True)
class SweepOptions:
    """The optional knobs of ``sweep_old_logs``; ``None`` means the module default."""

    keep: int | None = None
    days: float | None = None
    now: float | None = None
    skip: str | Path | None = None
    enabled: bool | None = None


def _sweep_plan(opts: SweepOptions) -> tuple[int, float, float]:
    """The (keep_n, cutoff, now_ts) of a sweep, module defaults filled in."""
    keep_n = RETENTION_MAX_FILES if opts.keep is None else int(opts.keep)
    max_age = RETENTION_DAYS if opts.days is None else float(opts.days)
    now_ts = time.time() if opts.now is None else float(opts.now)
    return keep_n, now_ts - max_age * 86400.0, now_ts


# Delete this prefix's stale per-process files; return the names removed.
#
# Per-process files would otherwise accumulate one per client per restart. A file
# is removed when it is older than *days* OR beyond the newest *keep* by mtime.
#
# Four guarantees this function must keep, because it DELETES:
# * a file whose pid is still ALIVE is never removed (see ``_pid_is_alive``);
# * the active process's file (and its rotated backups) is never a candidate --
#   pass it as *skip*;
# * a file that cannot be deleted (locked on Windows, owned by another user,
#   already gone) is skipped, never raised on -- retention must never take down
#   a server or a supervisor;
# * the legacy SHARED ``qa-agents.log`` / ``launcher.log`` cannot match the glob
#   (``<prefix>-*``), so pre-existing evidence is left exactly where it is.
def sweep_old_logs(
    log_dir: str | Path,
    *,
    prefix: str = DEFAULT_PREFIX,
    **options: Any,
) -> list[str]:
    """Delete this prefix's stale per-process files; return the names removed."""
    opts = SweepOptions(**options)  # an unknown keyword is a TypeError, as before
    removed: list[str] = []
    try:
        if not (RETENTION_ENABLED if opts.enabled is None else opts.enabled):
            return removed
        keep_n, cutoff, now_ts = _sweep_plan(opts)
        active = Path(opts.skip).name if opts.skip else ""
        scan = SweepScan(Path(log_dir), prefix, active, now_ts)
        removed.extend(_delete_stale(_sweep_candidates(scan), keep_n, cutoff))
    except (OSError, TypeError, ValueError, OverflowError):
        logger.debug("the log retention sweep failed", exc_info=True)
    return removed


def _off_posix() -> bool:
    """Legacy name of :func:`off_posix` (WX removes it)."""
    return off_posix()
