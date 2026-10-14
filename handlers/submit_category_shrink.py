"""The shrink guard: refuse a re-submission that would DELETE staged cases."""

from __future__ import annotations

import logging
from typing import Any

from config.settings_guard import never_raise
from handlers import _common, _reply
from handlers.submit_category_types import _CategoryArgs
from tools import prep_store

logger = logging.getLogger("tools.mcp_handlers")


# How many cases are ALREADY staged for *category_name* on this prep.
#
# Reads what ``prep_store.load_submissions`` returned. Returns 0 for an absent
# row, an unreadable payload, or ANY error -- and 0 disables BOTH the duplicate
# disclosure and the shrink guard, so a store hiccup can never refuse a genuine
# submission (the fail-OPEN direction, pinned end to end by a test). Never
# raises.
def _prior_category_count(rows_result: Any, category_name: str) -> int:
    """How many cases are ALREADY staged for *category_name* on this prep."""
    with never_raise(logger, "prior staged category count failed", exc_info=True):
        rows = (rows_result or {}).get("content") or []
        want = str(category_name or "").strip()
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("category_name") or "").strip() != want:
                continue
            payload = row.get("payload")
            if not isinstance(payload, dict):
                return 0
            return len(payload.get("test_cases") or [])
    return 0


def _shrink_head(cat: str, prior: int, new: int) -> str:
    """The opening of the shrink refusal: what was refused and why."""
    return (
        "## ⛔ Re-submission refused: it would SHRINK "
        f"**{cat}**\n\n"
        f"**{prior} case(s)** are already staged for this category and this "
        f"submission carries only **{new}**. Accepting it would DELETE "
        f"{prior - new} already-validated case(s) -- the staging write is "
        "replace-by-category, newest wins -- and the usual cause is an output "
        "that was cut short, not a deliberate trim.\n\n"
    )


def _shrink_choices(cat: str, prep_id: str, prior: int) -> str:
    """The shrink refusal's two ways forward."""
    return (
        "**Nothing was discarded and nothing was saved.** The "
        f"{prior}-case row for **{cat}** is still staged for prep_id "
        f"`{prep_id}`. Choose ONE:\n\n"
        f"- **Re-send the FULL category** ({prior} or more cases) with "
        "`qa_submit_category` -- do this if the last output was truncated.\n"
        "- **Keep the smaller set on purpose**: call `qa_submit_category` again "
        "with the SAME arguments plus `replace_smaller=true`. That is an "
        "explicit decision to drop the extra case(s).\n\n"
        "> ℹ️  If the expected category set is already complete, do "
        "not regenerate anything -- finalize with `qa_submit_suite`.\n\n"
    )


# The GATE reply for a re-submission that would SHRINK a staged category.
#
# A refusal, NOT a disclosure: nothing was written, so the already-accepted row
# is intact. Never a dead end -- it names the override -- and never a lost
# generation: the cases stay in the caller's own context, so re-sending them
# costs no new generation. 2026-08-04 evidence: on prep 59ab1c49 a `cases: 1`
# row silently replaced a good 12-case one. A plain return, so this function
# cannot break handle_submit_category's never-raise contract.
def shrinking_resubmit_reply(args: _CategoryArgs, prior: int, new: int) -> str:
    """The GATE reply for a re-submission that would SHRINK a staged category."""
    # Bounded, fence-safe display of the caller's own value -- see
    # `_unrecognized_names_line` for the invariant and why there is one
    # helper rather than a fix per site.
    _cat = _reply._clip_echo(args.category_name, 60)
    return _shrink_head(_cat, prior, new) + _shrink_choices(_cat, args.prep_id, prior)


# The shrink-guard refusal: touch the prep, audit it, return the reply.
#
# A GATE, not a disclosure: it REFUSES and saves NOTHING, so the
# already-accepted row survives. Never a dead end (the reply names
# replace_smaller=true) and never raises (a plain return).
#
# Touch the prep (review L2): a refusal returns before
# save_submission, which is the only other place the activity
# timestamp is written, so without this a run of refusals is
# INVISIBLE activity under QA_PREP_SLIDING_TTL_ENABLED -- the prep
# could expire mid-argument. touch_prep is itself a never-raising
# no-op when neither touch flag is on.
async def _refuse_shrinking_resubmit(args: _CategoryArgs, prior: int, new: int) -> str:
    """The shrink-guard refusal: touch the prep, audit it, return the reply."""
    await prep_store.touch_prep(args.prep_id)
    await _common.audit(
        "mcp_submit_category_refused",
        entity_id=args.prep_id,
        detail={
            "category": args.category_name,
            "cases": new,
            "prior_cases": prior,
            "reason": "shrinking_resubmission",
        },
    )
    return shrinking_resubmit_reply(args, prior, new)
