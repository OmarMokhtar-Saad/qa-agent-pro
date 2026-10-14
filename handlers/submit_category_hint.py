"""The staged-rows aware "how to come back" hint of a refused finalize."""

from __future__ import annotations

import logging

from agents.host_category_calls import StagedHintIn
from config.settings_guard import never_raise
from tools import prep_store

logger = logging.getLogger("tools.mcp_handlers")


# How many per-category rows the server ALREADY holds for this prep.
#
# The refusal WORDING turns on it -- never a verdict. Telling a host to
# "resubmit the COMPLETE suite" when eight categories are already staged buys
# a second full generation pass for nothing and silently rewrites cases the
# tester has already reviewed; ``staged_resubmit_hint`` carries the measured
# audit.db evidence for that cost.
#
# Reads defensively: an unreadable store returns 0, which degrades to the
# resend wording rather than claiming work is held when it is not.
async def _staged_row_count(prep_id: str) -> int:
    """How many per-category rows the server ALREADY holds for this prep."""
    with never_raise(logger, "staged row count failed", exc_info=True):
        rows_res = await prep_store.load_submissions(prep_id)
        rows = (rows_res or {}).get("content") or []
        return sum(
            1
            for r in rows
            if isinstance(r, dict) and str(r.get("category_name") or "").strip()
        )
    return 0


async def _staged_names_for_hint(prep_id: str, staged_count: "int | None") -> list:
    """Staged category names (blank placeholders when only a count is known)."""
    if staged_count is not None:
        return [""] * max(0, int(staged_count))
    with never_raise(logger, "staged-rows resubmit hint failed", exc_info=True):
        rows_res = await prep_store.load_submissions(prep_id)
        rows = (rows_res or {}).get("content") or []
        return [
            str(r.get("category_name") or "").strip()
            for r in rows
            if isinstance(r, dict) and str(r.get("category_name") or "").strip()
        ]
    return []


def _resend_hint(prep_id: str, extra_field: str) -> str:
    """The hint when nothing is staged: resend the same suite."""
    if extra_field:
        return (
            f"resubmit the SAME suite with prep_id `{prep_id}` and a top-level "
            f"`{extra_field}`"
        )
    return f"resubmit the SAME suite with prep_id `{prep_id}`"


def _sidecar_hint(extra_field: str, field_shape: str) -> str:
    """The `suite_json` value of the sidecar-only retry."""
    return (
        '{"' + extra_field + '": ' + (field_shape or "...") + "}"
        if extra_field
        else '"" (empty)'
    )


# How a refused host should come back, given what the server already holds.
#
# MEASURED (audit.db, prep 2b0de0011a44, TICKET-3541): eight staged categories
# were refused for a missing ambiguity_result, and the host then RE-STAGED all
# eight (two with changed case counts) because it read "resubmit the SAME
# suite" literally. On the staged route the retry carries the missing field
# ALONE and the merge rebuilds from the held rows; ``extra_field`` names that
# top-level key (empty for an ack-only retry).
#
# ``field_shape`` describes what goes INSIDE that key, so the shape sits next
# to the key it describes and not after "and nothing else".
#
# ``staged_count`` lets a caller that already counted rows skip a second read.
# ``handle_submit_suite`` reports ZERO on the merged route (``has_full``:
# those rows were not used), and a re-read here would disagree with it.
#
# Never raises: an unreadable store falls back to the resend wording.
async def staged_resubmit_hint(args: StagedHintIn) -> str:
    """How a refused host should come back, given what the server already holds."""
    prep_id, extra_field = args.prep_id, args.extra_field
    staged = await _staged_names_for_hint(prep_id, args.staged_count)
    if not staged:
        return _resend_hint(prep_id, extra_field)
    sidecar = _sidecar_hint(extra_field, args.field_shape)
    return (
        f"**do NOT resend the cases** -- all {len(staged)} staged categor"
        f"{'y is' if len(staged) == 1 else 'ies are'} already held on prep "
        f"`{prep_id}` and the finalize rebuilds the suite from them. Call "
        f"`qa_submit_suite` with prep_id `{prep_id}` and `suite_json={sidecar}` "
        "and nothing else. Regenerating instead costs a second full pass AND "
        "silently changes cases the tester already reviewed"
    )
