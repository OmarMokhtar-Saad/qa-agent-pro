"""The per-run animation record: written BEFORE the scales change, read by the restore."""

from __future__ import annotations

import logging

from tools.mobile import run_records

logger = logging.getLogger("tools.mobile.animations")

#: The per-run record of the animation scales this run displaced.
RECORD_FILE = "animations.json"


def _record_path(run_id: str):
    return run_records.record_path(run_id, RECORD_FILE)


def _site() -> run_records.RecordSite:
    return run_records.RecordSite(_record_path, logger, "mobile.animations")


def read_record(run_id: str) -> dict:
    """The run's animation record, or ``{}``. Never raises."""
    return run_records.read_record(_site(), run_id)


def write_record(run_id: str, body: dict) -> dict:
    """Write the run's animation record through run_store's own writer."""
    return run_records.write_record(_site(), run_id, body)


def clear_record(run_id: str) -> None:
    """Drop the record. A missing file is success: the point is that it is gone."""
    run_records.clear_record(_site(), run_id)
