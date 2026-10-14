"""Updater lock-contention diagnostics, logs only (split from tools/updater.py)."""

from __future__ import annotations

import time
from pathlib import Path

from tools.updater_log import logger

#: How long a holder may keep the lock before a loser reports it as wedged.
#: An ESTIMATE, not a measurement -- no swap on this install has ever been
#: timed. It is deliberately far above any plausible swap so the CRITICAL line
#: below means "something is wrong, look at this" and never "this is busy".
HELD_TOO_LONG_S = 600


# A human-readable description of who holds the lock, for LOGS ONLY.
#
# Nothing branches on this. That is the whole point: the previous design read
# this body to decide whether the holder was alive and whether the lock could
# be taken, and got a defect in each direction. It is diagnostics now, and a
# wrong or missing answer costs a less specific log line and nothing else.
def _lock_holder_note(lock_path: Path) -> str:
    """A human-readable description of who holds the lock, for LOGS ONLY."""
    try:
        body = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown holder"
    head, _, stamp = body.partition(" ")
    try:
        held = max(0.0, time.time() - float(stamp))
        seconds = str(int(held))
    except (ValueError, OverflowError):
        return "holder " + (head[:40] or "unknown")
    return "holder " + (head[:40] or "unknown") + ", holding for " + seconds + "s"


def _held_too_long(lock_path: Path) -> float:
    """Seconds the current holder has held the lock, or ``0.0`` if unknown."""
    try:
        body = lock_path.read_text(encoding="utf-8").strip()
        return max(0.0, time.time() - float(body.partition(" ")[2]))
    except (OSError, ValueError):
        return 0.0


def _warn_lock_is_dir(lock_path: Path) -> None:
    logger.warning(
        "tools.updater: %s is a DIRECTORY, so no update can take the lock. "
        "Remove it to let updates resume.",
        lock_path,
    )


def _warn_open_failed(install_dir: Path, exc: OSError) -> None:
    """Warn that the update lock file could not be opened."""
    logger.warning(
        "tools.updater: could not open an update lock in %s (%s); skipping this pass.",
        install_dir,
        exc,
    )


# NOT a force-release: breaking a lock whose holder may be
# mid-swap is the concurrent overlay this exists to prevent,
# and "is the holder wedged" is the same liveness question the
# file has already proved it cannot answer. So this reports,
# and a human decides.
def _log_lock_contention(lock_path: Path, install_dir: Path) -> None:
    """Report that another process holds the lock (a critical line if wedged)."""
    held = _held_too_long(lock_path)
    if held > HELD_TOO_LONG_S:
        logger.critical(
            "tools.updater: the update lock in %s has been held for "
            "%.0fs (%s). This install has stopped updating; check "
            "whether that process is wedged.",
            install_dir,
            held,
            _lock_holder_note(lock_path),
        )
    else:
        logger.info(
            "tools.updater: another process is applying an update to "
            "%s (%s); skipping this pass.",
            install_dir,
            _lock_holder_note(lock_path),
        )
