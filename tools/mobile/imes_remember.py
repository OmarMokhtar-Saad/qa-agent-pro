"""Remember the tester's keyboard, once per run, before anything changes."""

from __future__ import annotations

import logging
import os
import time

from tools.mobile import ime, imes_record

logger = logging.getLogger("tools.mobile.ime_session")


def _record_body(serial: str, kept: dict) -> dict:
    """The run's IME record: whose keyboard was displaced, and by which process."""
    return {
        "serial": str(serial),
        "previous": str(kept.get("previous") or ""),
        "was_ours": bool(kept.get("was_ours")),
        "selected_at": time.time(),
        # WHO to prove gone before anyone undoes this. Without it a
        # later sweep can only ask whether the device lock is free,
        # and a lock can be free while its writer is alive between
        # two calls. A record carrying no pid is never swept.
        "pid": os.getpid(),
    }


def _write_refusal(written: dict) -> dict:
    """The refusal when the record could not be written."""
    # A record we could not write is a restore we could not perform,
    # so this refuses rather than displacing a keyboard it has no way
    # to give back.
    return {
        "error": (
            "Refusing to select the QA input method: its record could "
            "not be written, so the keyboard you have now could not be "
            "restored afterwards (" + str(written["error"])[:120] + ")."
        ),
        "content": None,
    }


async def _remember_keyboard(serial: str, run_id: str) -> dict | None:
    """Record the tester's keyboard once per run; an error result, else ``None``."""
    if imes_record.read_record(run_id):
        return None
    remembered = await ime.remember_previous(serial)
    if remembered.get("error"):
        return remembered
    kept = remembered.get("content") or {}
    written = imes_record.write_record(run_id, _record_body(serial, kept))
    if written.get("error"):
        return _write_refusal(written)
    return None
