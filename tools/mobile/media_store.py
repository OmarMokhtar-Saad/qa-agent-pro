"""Case-record side of the media lane: read the stored records, append new ones."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from tools.mobile import run_store

logger = logging.getLogger("tools.mobile.media")


def records_of(run_id: str, tc_id: str) -> list:
    """Every media record already stored for a case. Never raises."""
    try:
        body = (run_store.read_case(run_id, tc_id) or {}).get("content")
        body = body if isinstance(body, dict) else {}
        return [
            item for item in list(body.get("media") or []) if isinstance(item, dict)
        ]
    except Exception:
        logger.exception("mobile.media.records_of failed")
        return []


def _held(fresh: dict, records: list) -> list:
    """The case's media list with every dict of *records* appended."""
    held = [item for item in list(fresh.get("media") or []) if isinstance(item, dict)]
    held.extend(item for item in list(records or []) if isinstance(item, dict))
    return held


def _warn_no_case(run_id: str, tc_id: str, records: list) -> None:
    """Say that records were produced for a case that has no record."""
    # SAY SO. This is the one path where records are produced and then
    # dropped, and `finish_step`'s caller ignores the result by design
    # -- so a bare return here is a silent loss: the files are on disk
    # and nothing indexes them, and the report renders as though no
    # recording was ever attempted. It happens when no case record
    # exists yet, which in production means `start_case` did not run or
    # its write failed. The pictures are still on disk beside the run.
    logger.warning(
        "mobile.media.remember: no case record for %s/%s; %d media "
        "record(s) not indexed",
        run_id,
        tc_id,
        len([item for item in list(records or []) if isinstance(item, dict)]),
    )


# The case record is the authority on what a case did, so the media index is
# not a second file: a second index would be a second producer of one fact.
# That makes ``case_runner._checkpoint`` a CONSUMER of this key -- it rebuilds
# the case body whole on every submit, so it carries ``media`` forward
# explicitly, and ``tests/mobile/test_mobile_media_checkpoint.py`` drives
# ``submit_case`` end to end to keep that true.
def remember(run_id: str, tc_id: str, records: list) -> dict:
    """Append records to the case the checkpoint already owns."""
    with never_raise(
        logger, "mobile.media.remember failed: %s", level=logging.WARNING, with_exc=True
    ):
        fresh = (run_store.read_case(run_id, tc_id) or {}).get("content")
        if not isinstance(fresh, dict):
            _warn_no_case(run_id, tc_id, records)
            return {"error": None, "content": []}
        held = _held(fresh, records)
        fresh["media"] = held
        run_store.write_case(run_id, tc_id, fresh)
        return {"error": None, "content": held}
    return {"error": None, "content": []}
