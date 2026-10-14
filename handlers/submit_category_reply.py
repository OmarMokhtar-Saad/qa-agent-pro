"""The success reply of a recorded category: notes, summary and route."""

from __future__ import annotations

from agents.host_category_jobs import category_checklist_note, category_dedup_note
from handlers import _elicit, prep_status
from handlers.submit_category_dup import _category_dup_note, dup_shortlist_note
from handlers.submit_category_resubmit import category_resubmit_note
from handlers.submit_category_route import _category_route
from handlers.submit_category_types import (
    _CategoryArgs,
    _CategoryIntake,
    _CategoryStaged,
    _ReplyParts,
    _StagedView,
)
from handlers.submit_category_view import _staged_category_view, all_staged_banner


def _review_notes(parsed) -> str:
    """The dedup and checklist notes of the submitted suite."""
    # Duplicate review is only possible on the MERGED suite -- merge_category_rows
    # copies only test_cases and renumbers every tc_id -- so say the field
    # cannot be used here rather than swallowing it.
    note = category_dedup_note(parsed)
    # Residue R4: same class of silent drop for the checklist job's return
    # field -- _validate_suite pops it and merge_category_rows keeps only
    # test_cases, so a host that ignores the "use the finalize sidecar"
    # instruction lost it with no per-category signal at all. Self-silent
    # when the field is absent, so an ordinary submission is byte-identical,
    # and unconditional on purpose: the drop happens whatever the review
    # flags say.
    note += category_checklist_note(parsed)
    return note


async def _category_notes(args: _CategoryArgs, intake: _CategoryIntake) -> str:
    """Every note that LEADS the reply, in the order the host reads them."""
    # FIX 1: the resubmit disclosure LEADS `note`, ahead of the dropped-cases
    # line, because "you just replaced a staged row" changes how everything
    # below it should be read. "" for a first submit and for an ordinary
    # submission on a prep with no stamp, so those replies are byte-identical.
    note = category_resubmit_note(args, intake)
    note += prep_status.dropped_note(intake.parsed)
    note += await _category_dup_note(intake.parsed, intake.meta)
    note += _review_notes(intake.parsed)
    return note


def _render_category_reply(
    args: _CategoryArgs, view: _StagedView, parts: _ReplyParts
) -> str:
    """The success reply: `lead` (notes) + the recorded summary + `tail` (route)."""
    _cat = args.display
    prep_id = args.prep_id
    on_file = view.on_file
    staged_names = view.suffix
    return (
        f"{parts.lead}## ✅ Recorded {parts.case_count} case(s) for "
        f"**{_cat}**\n\n"
        f"Re-submitting **{_cat}** REPLACES its previous rows "
        "(newest wins).\n\n"
        f"**{on_file}** category row(s) staged for prep_id `{prep_id}`"
        f"{staged_names}.\n\n"
        f"{parts.tail}"
    )


# F4: tracked SEPARATELY from `note`, which also carries the dropped-cases
# disclosure -- keying the route wording off `note` would promise a review
# that never ran whenever cases were dropped.
# F11: whether a REVIEW IS AVAILABLE at all, independent of whether the
# host already sent the field. category_dedup_note is empty until the host
# sends duplicate_groups, which on THIS route it can never do -- so keying
# the route wording off the note steered testers away from the only route
# where the review works.
#
# Phase 2 (QA_DUP_SHORTLIST_ENABLED, default OFF): "" unless THIS
# submission completed the expected set and the prescreen found pairs.
#
# 2026-08-04: say COMPLETE out loud -- see all_staged_banner.
#
# A4: warn on EVERY category submit from an unrecognised client, not
# only at finalize -- "" (no-op) whenever this call's own client is a
# known editor host. Never gates: the row above is already saved.
async def _reply_parts(
    intake: _CategoryIntake, staged: _CategoryStaged, note: str
) -> _ReplyParts:
    """The lead notes, the route and the case count of the success reply."""
    route = _category_route(review_available=True)
    rows_content = staged.rows.get("content") or []
    _shortlist = await dup_shortlist_note(intake.meta_raw, rows_content)
    _staged_all = all_staged_banner(intake.meta_raw, rows_content)
    _client_warn = _elicit.unrecognized_client_note(
        staged.provenance, context="category"
    )
    return _ReplyParts(
        len(intake.cases_json),
        f"{_client_warn}{_staged_all}{note}",
        f"{route}{_shortlist}",
    )


async def _category_reply(
    args: _CategoryArgs, intake: _CategoryIntake, staged: _CategoryStaged
) -> str:
    """Assemble the reply for a category that was recorded."""
    note = await _category_notes(args, intake)
    view = _staged_category_view(staged.rows, intake.meta, staged.on_file)
    parts = await _reply_parts(intake, staged, note + view.warning)
    return _render_category_reply(args, view, parts)
