"""Measurement leaf of handover_cost: payload -> Handover."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from tools.hcost_core import (
    _SHARED_TEXT_KEYS,
    Handover,
    _text_chars,
    payload_bytes,
)

logger = logging.getLogger("tools.handover_cost")


def _sizes(payload: object) -> tuple[int, int, int, int]:
    data = payload if isinstance(payload, dict) else {}
    categories = [c for c in (data.get("categories") or []) if isinstance(c, dict)]
    jobs = [j for j in (data.get("jobs_to_run") or []) if isinstance(j, dict)]
    shared = sum(_text_chars(data.get(k)) for k in _SHARED_TEXT_KEYS)
    shared += _text_chars(data.get("response_schema"))
    instruction = sum(_text_chars(c.get("instruction")) for c in categories)
    return len(categories), len(jobs), shared, instruction


# Measure one prepare payload.
#
# ``image_count`` / ``image_bytes`` describe the images that will ACTUALLY
# ride on the reply -- the caller passes the POST-budget figures, because
# only the MCP assembly layer knows which attachments survive the per-result
# byte budget and the count cap. Passing the pre-budget totals would
# over-report a handover that never happens.
#
# Never raises: on any internal error it returns an all-zero Handover, and
# ``handover_line`` then says the payload could not be sized rather than
# printing a wrong number.
#
def measure(payload: object, *, image_count: int = 0, image_bytes: int = 0) -> Handover:
    """Measure one prepare payload; never raises (all-zero Handover on error)."""
    # a measurement never breaks a prepare
    with never_raise(logger, "measuring the handover failed", exc_info=True):
        n, jobs, shared, instruction = _sizes(payload)
        return Handover(
            categories=n,
            step_zero_jobs=jobs,
            model_turns=n + jobs,
            shared_chars=shared,
            instruction_chars=instruction,
            total_input_chars=shared * n + instruction,
            payload_bytes=payload_bytes(payload),
            image_count=max(0, int(image_count or 0)),
            image_bytes=max(0, int(image_bytes or 0)),
        )
    return Handover()
