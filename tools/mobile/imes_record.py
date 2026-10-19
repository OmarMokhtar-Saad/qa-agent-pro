"""The QA IME's per-run records: whose keyboard we displaced, and the memoised failure."""

from __future__ import annotations

import logging
import time

from config.settings_guard import never_raise
from tools.mobile import run_io, run_records

logger = logging.getLogger("tools.mobile.ime_session")

#: The per-run record of whose keyboard we displaced.
#:
#: Beside the run's other artifacts and written through ``run_store``'s own
#: writer, so it inherits the redact pass and the atomic write everything else
#: in a run folder gets rather than growing a second writer with its own rules.
RECORD_FILE = "ime.json"

#: The run's MEMOISED failure to set the keyboard up (R1d). Its own file, NOT
#: ``ime.json``: that record means "there is a keyboard to give back" and the
#: restore path reads it, so a failure written there would be restored as one.
FALLBACK_FILE = "ime-fallback.json"

#: Seconds a memoised keyboard failure is trusted. A timeout on a still-booting
#: device, or a download that failed offline, may clear within a run; past this
#: the next typing case tries the install again.
FALLBACK_TTL_S = 600.0


def record_path(run_id: str):
    """Public seam: where *run_id*'s IME record lives."""
    return run_records.record_path(run_id, RECORD_FILE)


def _site() -> run_records.RecordSite:
    return run_records.RecordSite(record_path, logger, "mobile.ime_session")


def _fallback_path(run_id: str):
    return run_records.record_path(run_id, FALLBACK_FILE)


def adopt(owner: str, run_id: str) -> bool:
    """Hand the keyboard record (and a memoised failure) the pre-run *owner* wrote
    to *run_id*: the run's own ``ensure_ready`` finds the keyboard already
    selected and remembers nothing, so the record must be on the run already."""
    moved = run_records.move_record(_site(), owner, run_id)
    fallback = run_records.RecordSite(_fallback_path, logger, "mobile.ime_session")
    run_records.move_record(fallback, owner, run_id)
    return moved


def read_record(run_id: str) -> dict:
    """The run's IME record, or ``{}``. Never raises."""
    return run_records.read_record(_site(), run_id)


def write_record(run_id: str, body: dict) -> dict:
    """Write the run's IME record through run_store's own writer."""
    return run_records.write_record(_site(), run_id, body)


def clear_record(run_id: str) -> None:
    """Drop the record. A missing file is success: the point is that it is gone."""
    run_records.clear_record(_site(), run_id)


def read_fallback(run_id: str) -> dict:
    """The run's memoised keyboard failure, or ``{}`` once stale. Never raises."""
    if not str(run_id or ""):
        return {}
    with never_raise(
        logger, "mobile.ime_session.read_fallback failed", exc_info=True
    ) as g:
        body = run_io.read_json(run_records.record_path(run_id, FALLBACK_FILE))
    if g.failed:
        return {}
    if not isinstance(body, dict):
        return {}
    at = body.get("at")
    if not isinstance(at, (int, float)) or time.time() - float(at) > FALLBACK_TTL_S:
        return {}
    return body


def write_fallback(run_id: str, reason: str) -> None:
    """Remember that this run could not set the keyboard up. Never raises."""
    if not str(run_id or ""):
        return
    with never_raise(logger, "mobile.ime_session.write_fallback failed", exc_info=True):
        run_io.write_json(
            run_records.record_path(run_id, FALLBACK_FILE),
            {"error": str(reason)[:600], "at": time.time()},
        )
