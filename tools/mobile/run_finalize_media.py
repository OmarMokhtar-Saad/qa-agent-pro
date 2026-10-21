"""Close-out for media that never found a case record: a stub so totals count it."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from tools.mobile import media_pending, run_store

logger = logging.getLogger("tools.mobile.session")

STUB_REASON = "media recorded, no step ever landed"


def _stub(tc_id: str) -> dict:
    return {
        "tc_id": tc_id,
        "status": "abandoned",
        "ended_as": "media_only",
        "verdict": "",
        "reason": STUB_REASON,
        "trace": [],
    }


def close_held_media(run_id: str) -> None:
    """Write a stub case per held media key, then forget the hold. Never raises.

    ``write_case`` drains the held records into the stub's ``media`` list.
    """
    with never_raise(
        logger, "mobile.session: could not stub held media", level=logging.WARNING
    ):
        for tc_id in media_pending.held_cases(run_id):
            if not isinstance(run_store.read_case(run_id, tc_id).get("content"), dict):
                run_store.write_case(run_id, tc_id, _stub(tc_id))
    media_pending.drop_run(run_id)
