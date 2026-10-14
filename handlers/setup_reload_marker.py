"""The reload breadcrumb (moved from setup_check): written before a reload, read back after."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from handlers import _common
from tools import clock, updater

logger = logging.getLogger("tools.mcp_handlers")

# ops-8: a reload REPLACES this process, so the response that schedules one can
# never say whether it worked -- that response is already flushed by the time
# the exit happens. schedule_reload leaves this breadcrumb behind instead, and
# the NEXT handle_setup_check (a different process, after the launcher respawn)
# reads it back and reports the outcome. It lives beside the launcher's existing
# backups/session-state.json rather than in a new state directory.
_RELOAD_MARKER_NAME = "reload-marker.json"

# How long a marker stays actionable. A reload is a 2s exit plus a respawn plus
# an MCP handshake -- seconds, not minutes -- but a boot that reinstalls
# dependencies can take a couple of minutes, so allow generous slack. Past this
# the marker is assumed orphaned (the process died, or the editor was closed
# before anyone re-checked) and is discarded silently: an ancient breadcrumb
# must never warn "reload did not take effect" on every setup check forever.
_RELOAD_MARKER_TTL_S = 300.0


def _reload_marker_path() -> Path:
    """Where the reload breadcrumb lives. updater._INSTALL_DIR is read at call
    time, so tests can repoint the install dir."""

    return Path(updater._INSTALL_DIR) / "backups" / _RELOAD_MARKER_NAME


# ``version`` is the RUNNING version (BOOT_VERSION), never the on-disk one:
# after an update the on-disk VERSION is ALREADY the new value, so recording
# it would compare equal in the successor and read as a failed reload.
#
# Never raises -- an unwritable install dir only costs the confirmation line
# on the next call, and must never turn a working reload into a failed tool
# call.
def _write_reload_marker(reason: str) -> None:
    """Record what this process was running, moments before it exits to reload."""
    try:
        path = _reload_marker_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "version": _common.BOOT_VERSION,
                    "reason": reason,
                    "pid": os.getpid(),
                    "at": clock.now(),
                }
            ),
            encoding="utf-8",
        )
    except (OSError, TypeError, ValueError):
        logger.debug("could not write the reload marker", exc_info=True)


def consume_reload_marker() -> dict:
    """Read the reload breadcrumb and DELETE it, so an outcome is reported once.

    Returns ``{}`` when there is nothing to report: no marker, an unreadable or
    corrupt one, or one outside _RELOAD_MARKER_TTL_S (including a marker dated
    in the FUTURE, which means a clock change, not a reload). Never raises -- it
    feeds a report whose contract is "read-only and never raises"."""
    try:
        path = _reload_marker_path()
        raw = path.read_text(encoding="utf-8")
    except (OSError, TypeError, ValueError):
        return {}
    try:
        path.unlink()
    except OSError:
        # A marker that cannot be removed would repeat its verdict; the TTL
        # below bounds how long that can go on.
        logger.debug("could not remove the reload marker", exc_info=True)
    return _parse_reload_marker(raw)


def peek_reload_marker() -> dict:
    """Read the reload breadcrumb WITHOUT deleting it. build_server() uses this
    for its startup notice so qa-doctor's consume_reload_marker() still finds
    the marker and reports the reload outcome. Never raises."""
    try:
        raw = _reload_marker_path().read_text(encoding="utf-8")
    except (OSError, TypeError, ValueError):
        return {}
    return _parse_reload_marker(raw)


def _parse_reload_marker(raw: str) -> dict:
    """The marker dict, or {} when it is corrupt or outside
    _RELOAD_MARKER_TTL_S (a future timestamp is a clock change, not a
    reload). Never raises."""
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        age = clock.now() - float(data.get("at") or 0)
    except (ValueError, TypeError, OverflowError):
        logger.debug("unreadable reload marker ignored", exc_info=True)
        return {}
    if age < 0 or age > _RELOAD_MARKER_TTL_S:
        return {}
    return data
