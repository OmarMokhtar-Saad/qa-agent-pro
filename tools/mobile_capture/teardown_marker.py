"""The runtime proxy marker: read it, delete it, and the host boot id stamped in it."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from tools.mobile_capture import paths

logger = logging.getLogger("tools.mobile_capture.teardown")

HTTP_PROXY_SETTING = "http_proxy"
HTTP_PROXY_OFF = ":0"


def _read_marker(serial: str) -> dict | None:
    target = paths.proxy_marker(serial)
    try:
        if not target.is_file():
            return None
        body = json.loads(target.read_text(encoding="utf-8"))
        return body if isinstance(body, dict) else None
    except (OSError, ValueError, RecursionError):
        logger.warning("mobile_capture.teardown: unreadable marker for %s", serial)
        return None


def _delete_marker(serial: str) -> None:
    try:
        paths.proxy_marker(serial).unlink(missing_ok=True)
    except OSError:
        pass


def boot_id() -> str:
    """A string that changes across a host reboot, or ``""`` when unknowable.

    THE ONE PRODUCER of this fact: :mod:`tools.mobile_capture.proxy` stamps a
    marker with this at spawn time, and :func:`is_crash_residue` reads it back
    here rather than a second module deriving it a second way.
    """
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""
