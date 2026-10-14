"""The route + volume contract header for the host-mode PREPARE reply."""

from __future__ import annotations

import logging

from agents.host_submission import safe_prep_id
from config.settings_guard import never_raise
from handlers import export

logger = logging.getLogger("tools.mcp_handlers")


# The route + volume contract, stated ABOVE the payload body.
#
# 2026-08-12. A prep result is tens of KB and clients preview the first ~2 KB,
# so anything stated only inside the JSON is, in practice, unread: one session
# never called ``qa_get_category_job`` at all, submitted 27 cases against a
# 64-case contract, was refused three times, and shipped a hand-written CSV.
# The floor and the fan-out route are the two facts a host must have BEFORE it
# starts generating, so they go in the header.
#
# ``category_name="all"`` is REQUIRED, not optional: handle_get_category_job
# refuses an empty name outright, and the batch form is gated on
# ``category_name.lower() in ("all", "*")``. Advertising the call without it
# would recreate the dead-end read this block exists to prevent.
#
# The batch packet builder is flag-independent, and handle_submit_category
# serves an incremental host as well, so this route is valid on a default
# install where the payload itself carries no ``orchestration`` block.
#
# Pure. Returns [] whenever this payload carries no per-category floor, so a
# payload without a volume contract is byte-identical to today's output.
def _next_call_block(payload: object, prep_id: str) -> list:
    """The route + volume contract, stated ABOVE the payload body."""
    with never_raise(logger, "next-call block failed", exc_info=True):
        # Sanitise ONCE, here, rather than at each interpolation below: the id
        # is host-supplied and lands inside a code span in the first
        # instruction a host reads.
        prep_id = safe_prep_id(prep_id)
        if not isinstance(payload, dict):
            return []
        cats = payload.get("categories")
        if not isinstance(cats, list) or not cats:
            return []
        floors = _category_floors(cats)
        if not floors or len(floors) != len(cats):
            return []
        return _next_call_lines(prep_id, floors)
    return []


def _category_floors(cats: list) -> list[int]:
    """The positive ``min_cases`` of each dict category (others are skipped)."""
    floors = []
    for c in cats:
        if not isinstance(c, dict):
            continue
        try:
            n = int(c.get("min_cases") or 0)
        except (TypeError, ValueError, OverflowError):
            n = 0
        if n > 0:
            floors.append(n)
    return floors


# The contract is per-category, so the suite total is their SUM. Using
# min(floors) x len(cats) understated a non-uniform contract -- floors
# of 4 and 12 rendered "8 cases" when the payload asked for 16 -- and
# the same wrong number was repeated as the refusal threshold.
def _per_category_text(floors: list[int]) -> str:
    """The expected-total sentence fragment for one per-category floor list."""
    floor = min(floors)
    uniform = len(set(floors)) == 1
    total = sum(floors)
    return (
        f"{floor} case(s) per category \u00d7 {len(floors)} categories = "
        f"**{total} cases**"
        if uniform
        else f"**{total} cases**, per the per-category floors in the payload "
        f"(from {floor} to {max(floors)})"
    )


def _nc_head(per_category: str, prep_id: str) -> list:
    """The NEXT CALL banner and step 1 (the batch packet call)."""
    return [
        "",
        f"**NEXT CALL -- do not skip.** This suite is expected to reach "
        f"{per_category}.",
        "",
        f"1. Call `qa_get_category_job` with prep_id `{prep_id}` and "
        '`category_name="all"` -- ONE call returns every category packet.',
    ]


def _nc_step2(floor: int, uniform: bool) -> str:
    """Step 2: the per-category generation floor."""
    return (
        f"2. Generate each category to at least {floor} cases."
        if uniform
        else "2. Generate each category to at least its own `min_cases` -- they differ."
    )


# The route named here is the one the host will take: this block is
# the first thing it reads, by design. Which route is correct is
# NOT this function's decision -- host_mode owns it, and gets it
# right for both seam states -- so ask, never hardcode.
def _nc_step3() -> str:
    """Step 3: submit per category, then the finalize route."""
    return (
        "3. Call `qa_submit_category` once per category, then "
        + export._finalize_route_phrase()
        + "."
    )


def _nc_tail(total: int) -> list:
    """The closing paragraph on the single-call alternative and its refusal."""
    return [
        "",
        "Merging everything into a single `qa_submit_suite` call is also "
        f"allowed, but a suite materially below {total} cases is refused "
        "-- a partly-generated suite is the usual cause. If the tester has "
        "seen the shortfall and wants the smaller suite anyway, that "
        "refusal can be acknowledged; do not decide it yourself.",
    ]


def _next_call_lines(prep_id: str, floors: list[int]) -> list:
    """The NEXT CALL lines for one per-category floor list (one floor per category)."""
    per_category = _per_category_text(floors)
    floor = min(floors)
    uniform = len(set(floors)) == 1
    return [
        *_nc_head(per_category, prep_id),
        _nc_step2(floor, uniform),
        _nc_step3(),
        *_nc_tail(sum(floors)),
    ]
