"""The per-run a11y record: written BEFORE the setting changes, read by the restore."""

from __future__ import annotations

import logging

from tools.mobile import run_records

logger = logging.getLogger("tools.mobile.a11y")

RECORD_FILE = "a11y.json"


def record_path(run_id: str):
    return run_records.record_path(run_id, RECORD_FILE)


def _site() -> run_records.RecordSite:
    return run_records.RecordSite(record_path, logger, "mobile.a11y")


def read_record(run_id: str) -> dict:
    return run_records.read_record(_site(), run_id)


def write_record(run_id: str, body: dict) -> dict:
    return run_records.write_record(_site(), run_id, body)


def clear_record(run_id: str) -> None:
    run_records.clear_record(_site(), run_id)
