"""The local on-disk marker of what the certificate install last did for a device."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from tools.mobile_capture import paths

logger = logging.getLogger("tools.mobile_capture.cert")


def _marker_path(serial: str) -> Path:
    return paths.cert_marker(str(serial or ""))


def read_cert_marker(serial: str) -> dict | None:
    target = _marker_path(serial)
    try:
        if not target.is_file():
            return None
        body = json.loads(target.read_text(encoding="utf-8"))
        return body if isinstance(body, dict) else None
    except (OSError, ValueError, RecursionError):
        logger.warning("mobile_capture.cert: unreadable marker for %s", serial)
        return None


def _write_marker(serial: str, body: dict) -> None:
    target = _marker_path(serial)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(body, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, target)


def _clear_marker(serial: str) -> None:
    target = _marker_path(serial)
    try:
        target.unlink(missing_ok=True)
    except OSError:
        logger.warning("mobile_capture.cert: could not remove marker for %s", serial)


def _rc(body: dict) -> int:
    try:
        return int(body.get("rc") or 0)
    except (TypeError, ValueError, OverflowError):
        return 1
