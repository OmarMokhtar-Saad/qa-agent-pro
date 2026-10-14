"""The intake of a category submission: load the prep, parse, apply the shrink gate.

KNOWN LIMIT (review M3): the read->compare->write window here is NOT atomic
-- prep_store has no application lock -- so two CONCURRENT submits of the
SAME category can both read the prior count before either writes and the
truncated one can still land last. It fails OPEN (worst case is the
pre-2026-08-09 behaviour). See QA_HOST_CATEGORY_SHRINK_GUARD_ENABLED in
config/settings.py for why an in-process lock is deliberately NOT used.
"""

from __future__ import annotations

from agents.host_prep_serde import PrepSerdeError
from agents.host_submission import ParsedSubmission, parse_host_suite
from handlers import prep_status
from handlers.submit_category_shrink import (
    _prior_category_count,
    _refuse_shrinking_resubmit,
)
from handlers.submit_category_types import _CategoryArgs, _CategoryIntake
from tools import prep_store


def _read_submission(args: _CategoryArgs, suite_json) -> ParsedSubmission | str:
    """The parsed submission, or the refusal reply when it cannot be read."""
    try:
        return parse_host_suite(suite_json)
    except PrepSerdeError as exc:
        return f"⚠️ Could not read the submitted JSON for **{args.display}**: {exc}"


async def _shrink_gate(args: _CategoryArgs, intake: _CategoryIntake) -> str | None:
    """The refusal reply for a smaller re-submission the guard stamp forbids."""
    new = len(intake.cases_json)
    shrinking = bool(intake.prior > 0 and new < intake.prior)
    guard = bool(intake.meta.get("host_category_shrink_guard"))
    if shrinking and guard and not intake.overridden:
        return await _refuse_shrinking_resubmit(args, intake.prior, new)
    return None


# Fix 5b: without the finalized refusal, rows could be staged onto a finalized
# prep and a later empty finalize would build a SECOND suite from a subset of
# them.
#
# FIX 1 (2026-08-09): what is ALREADY staged for THIS category, read once BEFORE
# the INSERT OR REPLACE write, so the handler can still see the row it is about
# to overwrite. Both decisions are keyed off the prep's META STAMP rather than a
# live flag (see `handle_submit_category`'s docstring), and _prior_category_count
# returns 0 on ANY trouble, which disables both -- a store hiccup must never
# refuse a genuine submission. This is a SECOND load_submissions on this path
# (the counting one in `_stage_category` is today's only one); the two cannot be
# merged, because this one must observe the PRE-write state and that one must
# observe the POST-write state.
#
# Returns the refusal reply for every refusal, else the facts the later steps
# need.
async def _category_intake(
    args: _CategoryArgs, suite_json, replace_smaller: bool
) -> str | _CategoryIntake:
    """Load the open prep, parse the submission and apply the shrink gate."""
    envelope, refusal = await prep_status.load_open_prep(args.prep_id)
    if refusal:
        return refusal
    parsed = _read_submission(args, suite_json)
    if isinstance(parsed, str):
        return parsed
    cases_json = [tc.model_dump(mode="json") for tc in parsed.suite.test_cases]
    meta_raw = (envelope or {}).get("meta")
    rows = await prep_store.load_submissions(args.prep_id)
    prior = _prior_category_count(rows, args.category_name)
    overridden = bool(prior > 0 and len(cases_json) < prior and replace_smaller)
    intake = _CategoryIntake(
        parsed, cases_json, meta_raw, meta_raw or {}, prior, overridden
    )
    return await _shrink_gate(args, intake) or intake
