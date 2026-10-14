"""On-disk side of the per-device trust ledger: read, parse, atomic write.

Split out of :mod:`tools.mobile_capture.ledger`, which re-exports every public
name here. The logger keeps the ledger's name so log filters do not move.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from tools.mobile_capture import paths

logger = logging.getLogger("tools.mobile_capture.ledger")

#: Serial+fingerprint rows kept. Oldest ``updated_ms`` evicted first.
MAX_DEVICES: int = 200

#: The on-disk shape. A file whose version this reader does not know is read as
#: empty rather than guessed at: a wrong guess about a trust record is worse
#: than re-earning the trust.
LEDGER_VERSION: int = 1

#: ``_load_raw`` result for "no usable file" (absent or unreadable).
_NO_FILE = object()


def clock_ms() -> int:
    return int(time.time() * 1000)


def _tighten(tmp: Path) -> None:
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass


def _write_synced(tmp: Path, text: str) -> None:
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def write_ledger_json(target: Path, payload: object) -> None:
    """Temp file, fsync, ``os.replace`` -- the pattern ``mobile/run_store`` uses.

    Reimplemented rather than imported: ``run_store.write_ledger_json`` is private to
    a module about RUNS and redacts against a run's vocabulary. A device ledger
    holding a serial and a hash needs neither, and importing a private name
    across a package boundary would make a run module's refactor break device
    trust.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    _write_synced(tmp, text)
    _tighten(tmp)
    os.replace(tmp, target)


def _load_raw(target: Path) -> object:
    """The parsed file, or :data:`_NO_FILE` when it is absent or unreadable."""
    try:
        if not target.is_file():
            return _NO_FILE
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        logger.warning(
            "mobile_capture.ledger: %s is unreadable -- reading it as empty. "
            "Device trust will be re-earned on the next prepare.",
            target,
        )
        return _NO_FILE


def _valid_rows(raw: object, target: Path) -> list:
    """The well-formed rows of a parsed ledger file, or ``[]``."""
    if not isinstance(raw, dict) or raw.get("version") != LEDGER_VERSION:
        logger.warning(
            "mobile_capture.ledger: %s is not a ledger this reader knows -- "
            "reading it as empty",
            target,
        )
        return []
    rows = raw.get("devices")
    if not isinstance(rows, list):
        return []
    return [
        row
        for row in rows
        if isinstance(row, dict) and row.get("serial") and row.get("fingerprint")
    ]


def device_rows() -> list:
    """Every stored row, or ``[]``. Never raises."""
    target = paths.ledger_path()
    raw = _load_raw(target)
    if raw is _NO_FILE:
        return []
    return _valid_rows(raw, target)


def read() -> dict:
    """``{"error": None, "content": {"devices": [...]}}``."""
    try:
        return {"error": None, "content": {"devices": device_rows()}}
    except Exception as exc:
        logger.exception("mobile_capture.ledger.read failed")
        return {"error": str(exc), "content": None}


def _capped(rows: list) -> tuple[list, int]:
    """*rows* trimmed to ``MAX_DEVICES`` (oldest out) and the count evicted."""
    evicted = max(0, len(rows) - MAX_DEVICES)
    if evicted:
        logger.info(
            "mobile_capture.ledger: at MAX_DEVICES -- evicting %d oldest row(s)",
            evicted,
        )
        rows = rows[evicted:]
    return rows, evicted
