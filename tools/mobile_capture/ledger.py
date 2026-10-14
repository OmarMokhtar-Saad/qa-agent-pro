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

import logging
from typing import NamedTuple

from tools.mobile_capture import ledger_store as store
from tools.mobile_capture import paths
from tools.mobile_capture.ledger_store import (  # noqa: F401  re-exports: the on-disk side moved
    LEDGER_VERSION,
    MAX_DEVICES,
    clock_ms,
    device_rows,
    read,
    write_ledger_json,
)

logger = logging.getLogger(__name__)


class LedgerEntry(NamedTuple):
    """One device's earned tier: what :func:`record` stores."""

    serial: str
    fingerprint: str
    tier: str
    note: str = ""


def _save(rows: list) -> None:
    store.write_ledger_json(
        paths.ledger_path(), {"version": store.LEDGER_VERSION, "devices": rows}
    )


def _row_of(entry: LedgerEntry) -> dict:
    """The stored row for *entry*, stamped now."""
    return {
        "serial": entry.serial,
        "fingerprint": entry.fingerprint,
        "tier": entry.tier,
        "note": entry.note,
        "updated_ms": store.clock_ms(),
    }


def _merged_rows(rows: list, entry: LedgerEntry) -> list:
    """*rows* without *entry*'s own row, plus *entry*, oldest first."""
    own = (entry.serial, entry.fingerprint)
    kept = [r for r in rows if (r.get("serial"), r.get("fingerprint")) != own]
    kept.append(_row_of(entry))
    kept.sort(key=lambda row: row.get("updated_ms") or 0)
    return kept


def record(entry: LedgerEntry) -> dict:
    """Store the tier *serial* earned under *fingerprint*. Replaces its own row."""
    try:
        entry = LedgerEntry(*(str(part) for part in entry))
        if not entry.serial or not entry.fingerprint:
            return {
                "error": "serial and fingerprint are both required",
                "content": None,
            }
        rows, evicted = store._capped(_merged_rows(store.device_rows(), entry))
        _save(rows)
        return {"error": None, "content": {"stored": rows[-1], "evicted": evicted}}
    except Exception as exc:
        logger.exception("mobile_capture.ledger.record failed")
        return {"error": str(exc), "content": None}


def _found(row: dict) -> dict:
    """The ``tier_for`` reply for a row that matches serial AND fingerprint."""
    return {
        "error": None,
        "content": {
            "tier": row.get("tier"),
            "known_serial": True,
            "updated_ms": row.get("updated_ms"),
            "note": row.get("note", ""),
        },
    }


def _unmatched(known: bool) -> dict:
    """The ``tier_for`` reply when no row matches both serial and fingerprint."""
    return {
        "error": None,
        "content": {
            "tier": None,
            "known_serial": known,
            "updated_ms": None,
            "note": "",
        },
    }


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
        rows = store.device_rows()
        for row in rows:
            if row.get("serial") == serial and row.get("fingerprint") == fingerprint:
                return _found(row)
        return _unmatched(any(row.get("serial") == serial for row in rows))
    except Exception as exc:
        logger.exception("mobile_capture.ledger.tier_for failed")
        return {"error": str(exc), "content": None}


def _asked(serial: str) -> dict:
    """The ``asked_before`` reply: the first row for *serial*, under any CA."""
    for row in store.device_rows():
        if str(row.get("serial") or "") == serial:
            tier = str(row.get("tier") or "")
            return {"error": None, "content": {"asked": True, "tier": tier}}
    return {"error": None, "content": {"asked": False, "tier": ""}}


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
        return _asked(str(serial))
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
        rows = store.device_rows()
        kept = [row for row in rows if row.get("serial") != str(serial)]
        removed = len(rows) - len(kept)
        if removed:
            _save(kept)
        return {"error": None, "content": {"removed": removed}}
    except Exception as exc:
        logger.exception("mobile_capture.ledger.forget failed")
        return {"error": str(exc), "content": None}
