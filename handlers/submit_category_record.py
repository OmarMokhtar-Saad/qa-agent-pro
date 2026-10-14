"""Record ONE category's cases for a weaker host that submits incrementally.

2026-08-09 (Batch 3, FIX 1). A re-submission of an ALREADY-STAGED category is
no longer silent. When this prep's meta stamps ``host_category_resubmit_note``
the reply says plainly that this REPLACED an existing row and how many cases
each carried; when it stamps ``host_category_shrink_guard`` a re-submission
carrying FEWER cases than the row it would replace is REFUSED -- nothing is
saved and the good row survives -- unless the caller passes
``replace_smaller=True``, which is itself always disclosed. Both decisions
read the prep's META STAMP, never a live settings flag, so a mid-flow .env
flip or a launcher auto-update between prepare and submit cannot change an
in-flight prep, and an OLD envelope (carrying neither key) behaves exactly as
before.

The read->compare->write window is NOT atomic: see ``_category_intake`` for
the KNOWN LIMIT (review M3).

Validates the prep still exists, parses + salvages the submitted JSON with the
ops-3c parser (UNTRUSTED-safe: json.loads only, size-capped), and stores the
validated cases keyed UNIQUE per (prep_id, category_name) via
prep_store.save_submission. That row is INSERT OR REPLACE, so re-submitting the
same category REPLACES the earlier one (newest wins) -- the reply says so.
Never raises."""

from __future__ import annotations

from agents.host_submission import safe_prep_id
from handlers import _common, _elicit
from handlers._boundary import handler_guard
from handlers.submit_category_args import _category_submit_args
from handlers.submit_category_intake import _category_intake
from handlers.submit_category_reply import _category_reply
from handlers.submit_category_types import (
    _CategoryArgs,
    _CategoryIntake,
    _CategoryStaged,
)
from tools import dispatch_guard, prep_store
from tools.error_boundary import text_reply


# # FIX 1: the 2026-08-04 trail could not tell a first submit from
# the fifth -- every row looked identical -- so the `cases: 1` row
# that replaced a 12-case one was invisible. Added ONLY when this
# submission really did replace a staged row, so a FIRST submit's
# row stays byte-identical to today's. Deliberately NOT gated on
# either meta stamp (review M1): forensic fidelity must not depend
# on a disclosure flag, so an OLD unstamped prep's re-submission
# gains these keys too -- the REPLY is what stays byte-identical.
# **({"prior_cases": prior, "replaced": True} if prior > 0 else {}),
# Review M4: the ONE event that deliberately destroyed validated
# cases must not look like a benign equal-or-larger re-submission.
def _audit_extras(prior: int, cases: int, overridden: bool) -> dict:
    """The audit keys added only when a staged row was replaced."""
    return {
        **({"prior_cases": prior, "replaced": True} if prior > 0 else {}),
        **({"replace_smaller": True, "shrunk": prior - cases} if overridden else {}),
    }


def _category_audit_detail(args: _CategoryArgs, intake: _CategoryIntake) -> dict:
    """The audit detail for a recorded category, in its fixed key order."""
    cases = len(intake.cases_json)
    return {
        "category": args.category_name,
        "cases": cases,
        **_audit_extras(intake.prior, cases, intake.overridden),
    }


async def _save_row(args: _CategoryArgs, intake: _CategoryIntake, client: str) -> dict:
    """Write the category row (INSERT OR REPLACE) with this call's client name."""
    return await prep_store.save_submission(
        args.prep_id,
        args.category_name,
        {
            "test_cases": intake.cases_json,
            "dropped_count": intake.parsed.dropped_count,
            "client_name": client,
        },
    )


# A2/A4 (2026-09-26, v1.98 scope A): record THIS call's own client so
# a later finalize from a different process can union every
# contributor instead of stamping only the finalizing client (the
# laundering path A2/N2 fixes at finalize time -- see
# union_provenance).
async def _stage_category(
    args: _CategoryArgs, intake: _CategoryIntake
) -> str | _CategoryStaged:
    """Save the row, re-read the staged rows and audit the recorded category."""
    provenance = _elicit._dispatch_provenance()
    saved = await _save_row(args, intake, provenance.get("client_name", ""))
    if saved.get("error"):
        return f"⚠️ Could not record **{args.display}**: {saved['error']}"
    rows = await prep_store.load_submissions(args.prep_id)
    on_file = len(rows.get("content") or [])
    await _common.audit(
        "mcp_submit_category",
        entity_id=args.prep_id,
        detail=_category_audit_detail(args, intake),
    )
    return _CategoryStaged(rows, on_file, provenance)


@handler_guard(
    "qa_submit_category",
    "handle_submit_category failed",
    text_reply("⚠️ Recording the category failed: "),
)
async def _record_category(
    args: _CategoryArgs, suite_json: str, replace_smaller: bool
) -> str:
    intake = await _category_intake(args, suite_json, replace_smaller)
    if isinstance(intake, str):
        return intake
    staged = await _stage_category(args, intake)
    if isinstance(staged, str):
        return staged
    return await _category_reply(args, intake, staged)


async def handle_submit_category(  # noqa: PLR0913 - MCP tool schema
    prep_id: str,
    category_name: str,
    suite_json,
    *,
    progress: _common.ProgressCb = None,
    replace_smaller: bool = False,
) -> str:
    """Record ONE category's cases for a weaker host that submits incrementally."""
    dispatch_guard.require_dispatched("qa_submit_category")
    # UNTRUSTED: the host-sent id is echoed in replies, so it is sanitised
    # HERE, once, rather than at every interpolation downstream.
    prep_id = safe_prep_id(prep_id)
    args = _category_submit_args(prep_id, category_name)
    if isinstance(args, str):
        return args
    return await _record_category(args, suite_json, replace_smaller)
