"""The per-DEVICE trust ledger: serial + CA fingerprint -> tier.

Device trust is per SERIAL and OUTLIVES every run, which is the whole reason
keeping the certificate is worth anything: a device that proved decryption once
should not re-ask on the next run or the next reconnect. What was OBSERVED is
per case and belongs to the run; it is never written here, so this file cannot
grow with traffic.

A row is keyed by serial AND fingerprint. A regenerated CA has a new
fingerprint and must NOT inherit the tier the old one earned -- the stored tier
is a claim about a certificate that is no longer on that device, and honouring
it would skip an install the device now needs.

Every function returns ``{"error", "content"}`` and never raises. A corrupt
ledger reads as EMPTY at WARNING rather than failing the caller: it is a cache
of a device fact that can always be re-earned, and refusing a run over it would
trade a re-ask for an outage.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from tools.mobile_capture import paths

logger = logging.getLogger(__name__)

#: Serial+fingerprint rows kept. Oldest ``updated_ms`` evicted first.
MAX_DEVICES: int = 200

#: The on-disk shape. A file whose version this reader does not know is read as
#: empty rather than guessed at: a wrong guess about a trust record is worse
#: than re-earning the trust.
LEDGER_VERSION: int = 1


def _now_ms() -> int:
    return int(time.time() * 1000)


def _write_json(target: Path, payload: object) -> None:
    """Temp file, fsync, ``os.replace`` -- the pattern ``mobile/run_store`` uses.

    Reimplemented rather than imported: ``run_store._write_json`` is private to
    a module about RUNS and redacts against a run's vocabulary. A device ledger
    holding a serial and a hash needs neither, and importing a private name
    across a package boundary would make a run module's refactor break device
    trust.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, target)


def _rows() -> list:
    """Every stored row, or ``[]``. Never raises."""
    target = paths.ledger_path()
    try:
        if not target.is_file():
            return []
        raw = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        logger.warning(
            "mobile_capture.ledger: %s is unreadable -- reading it as empty. "
            "Device trust will be re-earned on the next prepare.",
            target,
        )
        return []
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


def read() -> dict:
    """``{"error": None, "content": {"devices": [...]}}``."""
    try:
        return {"error": None, "content": {"devices": _rows()}}
    except Exception as exc:
        logger.exception("mobile_capture.ledger.read failed")
        return {"error": str(exc), "content": None}


def record(serial: str, fingerprint: str, tier: str, *, note: str = "") -> dict:
    """Store the tier *serial* earned under *fingerprint*. Replaces its own row."""
    try:
        serial = str(serial)
        fingerprint = str(fingerprint)
        if not serial or not fingerprint:
            return {
                "error": "serial and fingerprint are both required",
                "content": None,
            }
        rows = [
            row
            for row in _rows()
            if not (
                row.get("serial") == serial and row.get("fingerprint") == fingerprint
            )
        ]
        rows.append(
            {
                "serial": serial,
                "fingerprint": fingerprint,
                "tier": str(tier),
                "note": str(note),
                "updated_ms": _now_ms(),
            }
        )
        rows.sort(key=lambda row: row.get("updated_ms") or 0)
        evicted = max(0, len(rows) - MAX_DEVICES)
        if evicted:
            logger.info(
                "mobile_capture.ledger: at MAX_DEVICES -- evicting %d oldest row(s)",
                evicted,
            )
            rows = rows[evicted:]
        _write_json(
            paths.ledger_path(), {"version": LEDGER_VERSION, "devices": rows}
        )
        return {"error": None, "content": {"stored": rows[-1], "evicted": evicted}}
    except Exception as exc:
        logger.exception("mobile_capture.ledger.record failed")
        return {"error": str(exc), "content": None}


def tier_for(serial: str, fingerprint: str) -> dict:
    """The tier *serial* earned UNDER THIS CA, or ``None``.

    A fingerprint MISMATCH is not a miss to be papered over: the reply carries
    ``known_serial`` so a caller can say "this device was trusted under a
    DIFFERENT certificate" rather than re-asking with no explanation. It never
    returns the stored tier for a different fingerprint.
    """
    try:
        serial = str(serial)
        fingerprint = str(fingerprint)
        rows = _rows()
        for row in rows:
            if row.get("serial") == serial and row.get("fingerprint") == fingerprint:
                return {
                    "error": None,
                    "content": {
                        "tier": row.get("tier"),
                        "known_serial": True,
                        "updated_ms": row.get("updated_ms"),
                        "note": row.get("note", ""),
                    },
                }
        known = any(row.get("serial") == serial for row in rows)
        return {
            "error": None,
            "content": {
                "tier": None,
                "known_serial": known,
                "updated_ms": None,
                "note": "",
            },
        }
    except Exception as exc:
        logger.exception("mobile_capture.ledger.tier_for failed")
        return {"error": str(exc), "content": None}


def asked_before(serial: str) -> dict:
    """Has this device ever been ANSWERED about, under any certificate?

    Deliberately NOT ``tier_for``: that one refuses to report a tier earned
    under a different fingerprint, because trust does not survive a new CA.
    Whether the tester has already been ASKED is a different question with a
    different right answer -- a regenerated CA must not re-open a conversation
    the tester already closed. Reading it through ``tier_for`` would have made
    one function answer two questions, and the fingerprint clause would then be
    wrong for one of them.

    ``{"error", "content": {"asked": bool, "tier": str}}``; never raises.
    """
    try:
        serial = str(serial)
        for row in _rows():
            if str(row.get("serial") or "") == serial:
                return {
                    "error": None,
                    "content": {"asked": True, "tier": str(row.get("tier") or "")},
                }
        return {"error": None, "content": {"asked": False, "tier": ""}}
    except Exception as exc:
        logger.exception("mobile_capture.ledger.asked_before failed")
        return {"error": str(exc), "content": None}


def forget(serial: str) -> dict:
    """Drop EVERY row for *serial*, whatever fingerprint earned it.

    Every row, not just the current CA's: this is what the removal path calls
    after taking the certificate off the device, and leaving a row behind would
    let a later run believe in trust that has been uninstalled.
    """
    try:
        rows = _rows()
        kept = [row for row in rows if row.get("serial") != str(serial)]
        removed = len(rows) - len(kept)
        if removed:
            _write_json(
                paths.ledger_path(), {"version": LEDGER_VERSION, "devices": kept}
            )
        return {"error": None, "content": {"removed": removed}}
    except Exception as exc:
        logger.exception("mobile_capture.ledger.forget failed")
        return {"error": str(exc), "content": None}
