"""Moved from handlers (refactor P2): submit_category."""

from __future__ import annotations

import json
import logging
from typing import Any, NamedTuple

from agents.host_category_jobs import (
    category_checklist_note,
    category_dedup_note,
    normalize_category,
    prep_status_view,
)
from agents.host_dedup import (
    build_dup_shortlist,
    build_dup_shortlist_section,
    dup_shortlist_on,
)
from agents.host_jobs import ambiguity_notes_gen_note
from agents.host_prep_serde import PrepSerdeError
from agents.host_submission import parse_host_suite, safe_prep_id
from config.settings import settings
from handlers import (
    _common,
    _elicit,
    _reply,
    export,
    generate,
    generate_prepare,
    prep_status,
)
from tools import dispatch_guard, prep_store, step_assertion
from tools.suite_quality_signals import compute_suite_quality_advisories

logger = logging.getLogger("tools.mcp_handlers")


class _CategoryArgs(NamedTuple):
    """The host's ids after the entry gates: sanitised, canonical, echo-safe."""

    prep_id: str
    category_name: str
    display: str


class _CategoryIntake(NamedTuple):
    """What a category submission looked like BEFORE anything was written."""

    parsed: Any
    cases_json: list
    meta_raw: Any
    meta: dict
    prior: int
    overridden: bool


class _CategoryStaged(NamedTuple):
    """What the write left behind: the post-write rows, their count, the client."""

    rows: dict
    on_file: int
    provenance: dict


class _StagedView(NamedTuple):
    """The staged-category count, its name suffix and the unrecognised warning."""

    on_file: int
    suffix: str
    warning: str


# F4 / F11 / Fix 2 text for the reply's closing "Choose ONE route" block. These
# were locals of `handle_submit_category`; they are module constants now so the
# text is written once and `_category_route` stays a few lines long.
#
# Only the duplicate review runs on the merged suite now that the host coverage
# review is deleted (2026-08-12), so name just it.
_CATEGORY_REVIEW_LABEL = "duplicate review"

# UNUSED (kept verbatim, see the plan's Follow-ups). The duplicate review runs
# ONLY on the merged suite, so the two routes are a real CHOICE, and the branch
# that builds the route must fire on that fact rather than on review_note: the
# note is empty until the host sends the field, which is impossible on this
# route, so the old condition steered away from the only route that works (F11).
# F11/F4 (iteration 4): this GENERAL "how to keep your review" explanation must
# NOT name a review FIELD by its literal token. The per-submission note
# (category_dedup_note) is the only place a field name may appear, and only when
# THIS submission's own payload carried it; naming it here too would read as "a
# review already ran" or "you were supposed to send it here". Pinned by
# test_submit_category_is_silent_without_the_field in
# tests/test_host_dedup_review.py. The field NAME is taught once, at prepare
# time, by host_mode._HOST_DEDUP_INSTRUCTION.
_CATEGORY_SIDECAR_LINE = (
    " The empty finalize is not the only option here: to KEEP "
    "the duplicate review on THIS route, finalize instead with "
    "the small review SIDECAR object described in your "
    "preparation instructions -- that review field alone, with "
    "empty or absent `test_cases`. The server remaps its tc_ids "
    "across the merge, so staging categories does NOT forfeit "
    "that review. Send it even when you found nothing: an EMPTY "
    "review field still counts as a review, while sending nothing "
    "is recorded as no review at all."
)

# 2026-08-03 (Fix 2): when the duplicate review is ON, the EMPTY finalize is
# precisely the call that FORFEITS it -- so labelling that call "recommended"
# steered hosts into discarding a review the tester had switched on. Observed on
# run3 (TICKET-5645): 8 mutually blind categories, 98 cases,
# QA_HOST_DEDUP_REVIEW_ENABLED=true, and zero cross-category review, because the
# host took the route this text recommended. Recommend the SIDECAR instead and
# name the empty form as the forfeiting alternative. The review FIELD is still
# never named here -- that is taught once at prepare time, pinned by
# test_submit_category_is_silent_without_the_field.
_CATEGORY_PRIMARY_BULLET = (
    "- **Finalize from these rows, keeping your review "
    "(recommended)**: when every category is staged, call "
    "`qa_submit_suite` with this prep_id and the small review "
    "SIDECAR object described in your preparation instructions "
    "(no `test_cases`). No case is re-sent, nothing already "
    "staged can be lost, and the review you were asked to run is "
    "carried across the merge. Found nothing? Send the sidecar "
    "anyway with its review field EMPTY -- an EMPTY review field "
    "still counts as a review.\n"
    "- **Or finalize with an EMPTY `suite_json` "
    '(`suite_json=""`)**: the same crash-safe merge, but it '
    "FORFEITS that review.\n"
)

_CATEGORY_ROUTE_REVIEW = (
    "Choose ONE route -- do not do both:\n\n"
    f"{_CATEGORY_PRIMARY_BULLET}"
    f"- **Or send one merged `suite_json`**: the {_CATEGORY_REVIEW_LABEL} "
    "runs on it, and the rows staged here are ignored -- but "
    "nothing at all is saved until that single call, so an "
    "interrupted chat loses every category.\n\n"
    "Sending both costs a round trip and the tokens to repeat "
    "every case: a non-empty `suite_json` is authoritative, so "
    "nothing staged here is merged in."
)


# Same invariant as `_unrecognized_names_line`, for the OTHER host-supplied
# value in the category handler: `display` goes through the bounded, fence-safe
# helper so a name carrying a fence cannot close its own span inside bold
# server-authored prose the host model reads as trusted.
#
# Computed AFTER the alias collapse, not on entry. The first cut of this fix
# snapshotted the value on entry and every reply then showed the tester's alias
# instead of the canonical category --
# `test_submit_category_records_and_reports` caught it, reading "**Positive**"
# where the contract says "**Positive / Happy Path**". The length gate already
# bounds the size; what this adds is fence safety, and it has to see the same
# value the replies mean.
def _category_submit_args(prep_id: str, category_name: str) -> str | _CategoryArgs:
    """Validate the (already safe_prep_id-gated) ids, or return the refusal reply."""
    category_name = (category_name or "").strip()
    if not prep_id:
        return (
            "⚠️ Missing prep_id. Prepare a generation first with "
            "`qa_prepare_test_cases`."
        )
    if not category_name:
        return (
            "⚠️ Missing category name. Pass the category you are submitting cases for."
        )
    # Collapse aliases onto the canonical UNIQUE key so parallel workers that
    # submit "Positive" vs "Positive / Happy Path" do not create two rows.
    _canon = normalize_category(category_name)
    if _canon:
        category_name = _canon
    # F20: a 300 KB `category_name` is not a category, and every reply on
    # this path -- the refusals AND the success summary -- echoed it whole.
    # One gate here bounds them all, rather than clipping a dozen strings.
    if len(category_name) > 200:
        return (
            "⚠️ That `category_name` is "
            + str(len(category_name))
            + " characters long, which is not a category name. Use one of "
            "the names from `orchestration.expected_categories` / "
            "`categories[].name` in the prepare payload. Received: "
            + _reply._clip_echo(category_name, 60)
        )
    display = _reply._clip_echo(category_name, 60)
    return _CategoryArgs(prep_id, category_name, display)


async def _refuse_shrinking_resubmit(args: _CategoryArgs, prior: int, new: int) -> str:
    """The shrink-guard refusal: touch the prep, audit it, return the reply."""
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
    await prep_store.touch_prep(args.prep_id)
    await _common._audit(
        "mcp_submit_category_refused",
        entity_id=args.prep_id,
        detail={
            "category": args.category_name,
            "cases": new,
            "prior_cases": prior,
            "reason": "shrinking_resubmission",
        },
    )
    return _shrinking_resubmit_reply(args.prep_id, args.category_name, prior, new)


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
async def _category_intake(
    args: _CategoryArgs, suite_json, replace_smaller: bool
) -> str | _CategoryIntake:
    """Load the open prep, parse the submission and apply the shrink gate.

    Returns the refusal reply for every refusal, else the facts the later steps
    need.

    KNOWN LIMIT (review M3): the read->compare->write window here is NOT atomic
    -- prep_store has no application lock -- so two CONCURRENT submits of the
    SAME category can both read the prior count before either writes and the
    truncated one can still land last. It fails OPEN (worst case is the
    pre-2026-08-09 behaviour). See QA_HOST_CATEGORY_SHRINK_GUARD_ENABLED in
    config/settings.py for why an in-process lock is deliberately NOT used.
    """
    envelope, refusal = await prep_status._load_open_prep(args.prep_id)
    if refusal:
        return refusal
    try:
        parsed = parse_host_suite(suite_json)
    except PrepSerdeError as exc:
        return f"⚠️ Could not read the submitted JSON for **{args.display}**: {exc}"
    cases_json = [tc.model_dump(mode="json") for tc in parsed.suite.test_cases]
    meta_raw = (envelope or {}).get("meta")
    meta = meta_raw or {}
    prior = _prior_category_count(
        await prep_store.load_submissions(args.prep_id), args.category_name
    )
    shrinking = bool(prior > 0 and len(cases_json) < prior)
    if (
        shrinking
        and bool(meta.get("host_category_shrink_guard"))
        and not replace_smaller
    ):
        return await _refuse_shrinking_resubmit(args, prior, len(cases_json))
    overridden = bool(shrinking and replace_smaller)
    return _CategoryIntake(parsed, cases_json, meta_raw, meta, prior, overridden)


def _category_audit_detail(args: _CategoryArgs, intake: _CategoryIntake) -> dict:
    """The audit detail for a recorded category, in its fixed key order."""
    prior = intake.prior
    cases = len(intake.cases_json)
    return {
        "category": args.category_name,
        "cases": cases,
        # FIX 1: the 2026-08-04 trail could not tell a first submit from
        # the fifth -- every row looked identical -- so the `cases: 1` row
        # that replaced a 12-case one was invisible. Added ONLY when this
        # submission really did replace a staged row, so a FIRST submit's
        # row stays byte-identical to today's. Deliberately NOT gated on
        # either meta stamp (review M1): forensic fidelity must not depend
        # on a disclosure flag, so an OLD unstamped prep's re-submission
        # gains these keys too -- the REPLY is what stays byte-identical.
        **({"prior_cases": prior, "replaced": True} if prior > 0 else {}),
        # Review M4: the ONE event that deliberately destroyed validated
        # cases must not look like a benign equal-or-larger re-submission.
        **(
            {"replace_smaller": True, "shrunk": prior - cases}
            if intake.overridden
            else {}
        ),
    }


async def _stage_category(
    args: _CategoryArgs, intake: _CategoryIntake
) -> str | _CategoryStaged:
    """Save the row, re-read the staged rows and audit the recorded category."""
    # A2/A4 (2026-09-26, v1.98 scope A): record THIS call's own client so
    # a later finalize from a different process can union every
    # contributor instead of stamping only the finalizing client (the
    # laundering path A2/N2 fixes at finalize time -- see
    # _union_provenance).
    provenance = _elicit._dispatch_provenance()
    saved = await prep_store.save_submission(
        args.prep_id,
        args.category_name,
        {
            "test_cases": intake.cases_json,
            "dropped_count": intake.parsed.dropped_count,
            "client_name": provenance.get("client_name", ""),
        },
    )
    if saved.get("error"):
        return f"⚠️ Could not record **{args.display}**: {saved['error']}"
    rows = await prep_store.load_submissions(args.prep_id)
    on_file = len(rows.get("content") or [])
    await _common._audit(
        "mcp_submit_category",
        entity_id=args.prep_id,
        detail=_category_audit_detail(args, intake),
    )
    return _CategoryStaged(rows, on_file, provenance)


async def _category_dup_note(parsed, meta: dict) -> str:
    """Best-effort duplicate check at category-submit time, as a reply block."""
    # v1.97.0 cursor-hardening (item 3a): no suite object exists yet here, so
    # the note folds straight into the reply string; the Generation Notes
    # sheet only ever sees the finalize-time (handle_submit_suite) signal.
    # VERIFIED 2026-09-26: prepare writes both keys into the envelope
    # meta ("source_text" / "source_url"); read defensively anyway so a
    # pre-1.97 envelope degrades to "" instead of raising.
    try:
        source = str(meta.get("source_url") or meta.get("source_text") or "")
        dup_note, _ = await generate._duplicate_case_note(
            getattr(getattr(parsed, "suite", None), "test_cases", None) or [],
            source,
        )
        if dup_note:
            return f"\n\n> ℹ️ {dup_note}"
    except Exception:
        logger.debug("category duplicate check failed", exc_info=True)
    return ""


async def _category_notes(args: _CategoryArgs, intake: _CategoryIntake) -> str:
    """Every note that LEADS the reply, in the order the host reads them."""
    # FIX 1: the resubmit disclosure LEADS `note`, ahead of the dropped-cases
    # line, because "you just replaced a staged row" changes how everything
    # below it should be read. "" for a first submit and for an ordinary
    # submission on a prep with no stamp, so those replies are byte-identical.
    note = _category_resubmit_note(
        intake.meta,
        args.category_name,
        intake.prior,
        len(intake.cases_json),
        overridden=intake.overridden,
    )
    note += prep_status._dropped_note(intake.parsed)
    note += await _category_dup_note(intake.parsed, intake.meta)
    # Duplicate review is only possible on the MERGED suite -- _merge_category_rows
    # copies only test_cases and renumbers every tc_id -- so say the field
    # cannot be used here rather than swallowing it.
    note += category_dedup_note(intake.parsed)
    # Residue R4: same class of silent drop for the checklist job's return
    # field -- _validate_suite pops it and _merge_category_rows keeps only
    # test_cases, so a host that ignores the "use the finalize sidecar"
    # instruction lost it with no per-category signal at all. Self-silent
    # when the field is absent, so an ordinary submission is byte-identical,
    # and unconditional on purpose: the drop happens whatever the review
    # flags say.
    note += category_checklist_note(intake.parsed)
    return note


# F4: name the categories already staged -- a host LLM re-reading this reply
# otherwise has only a bare count and cannot tell what is left.
#
# H1 (round-3 review, 2026-09-02). The suffix was
# `" (" + ", ".join(names) + ")"` -- the RAW row values, no cap and no fence
# handling, in a SUCCESS reply. The fourth occurrence of one class, and the one
# my own "the class is closed" commit missed, because its AST check only looked
# for a bare name inside an f-string and this is a `join`.
#
# Two routes were driven to it: an envelope carrying no `expected_categories`
# (which the code at the meta read calls a real state for an OLD envelope), and
# any exception in the block below -- whose `except` says "the count alone is
# still correct", while this raw value had already been assigned. Both produced
# a 1,701-byte reply with the fence intact and an injected `SYSTEM:` line
# standing as server prose.
#
# CANONICALISE first, which also settles the disagreement F11 was about:
# `qa_prep_status` renders the canonical `status["staged"]`, so this reply must
# not render something else. A name that does not normalise is still SHOWN --
# the tester needs to see what they typed -- but bounded and fence-safe, and the
# list is capped.
#
# F11 (2026-08-30): this reply counted every stored row, including a category
# name that normalises to nothing, and listed the bogus name among the staged
# categories -- while `qa_prep_status`, on the same prep a moment later,
# correctly reported `staged: 1/8` plus `unrecognized names: ...`. Two tools
# disagreeing about whether work was recorded is worse than either answer: the
# instructions tell a host to track progress from THESE replies (prep_status is
# optional), so it believed it was 2/8 done when it was 1/8. Same computation as
# prep_status, so they cannot drift again. Silent when the prep stamped no
# expected set (an old envelope), which keeps those replies byte-identical.
def _staged_category_view(rows: dict, meta: dict, on_file: int) -> _StagedView:
    """The staged count, name suffix and unrecognised-name warning for a reply."""
    staged_names = ""
    warning = ""
    try:
        names = [
            str(r.get("category_name", "")).strip()
            for r in (rows.get("content") or [])
            if isinstance(r, dict) and str(r.get("category_name", "")).strip()
        ]
        if names:
            staged_names = _reply._staged_names_suffix(names)
        _expected = list((meta or {}).get("expected_categories") or [])
        if _expected:
            _view = prep_status_view(expected=_expected, staged_raw_names=names)
            _recognized = list(_view.get("staged") or [])
            _unrecognized = list(_view.get("unrecognized") or [])
            if _unrecognized:
                on_file = len(_recognized)
                staged_names = (
                    " (" + ", ".join(_recognized) + ")" if _recognized else ""
                )
                warning = _reply._unrecognized_names_line(
                    _unrecognized,
                    prefix="> ⚠️  **Not a recognised category:** ",
                    suffix=(
                        ". Those rows are stored but will NOT be merged into "
                        "the suite and do NOT count toward the staged set. Use "
                        "a name from `orchestration.expected_categories` and "
                        "re-submit those cases under it.\n\n"
                    ),
                )
    except Exception:  # defensive -- the count alone is still correct
        logger.debug("could not list staged category names", exc_info=True)
    return _StagedView(on_file, staged_names, warning)


def _category_route(*, review_available: bool) -> str:
    """The closing "how to finalize" block of a category reply."""
    if review_available:
        return _CATEGORY_ROUTE_REVIEW
    return (
        "When every category is in, call `qa_submit_suite` with this "
        "prep_id and " + export._finalize_route_phrase(short=True) + " -- the rows "
        "staged above are merged for you.\n\n"
        "> ⚠️  Do NOT also send a full merged `suite_json`: a "
        "non-empty one is authoritative, so every row staged here is "
        "**ignored** and re-sending the cases costs a round trip and the "
        "tokens to repeat them."
    )


def _render_category_reply(
    args: _CategoryArgs, case_count: int, view: _StagedView, lead: str, tail: str
) -> str:
    """The success reply: `lead` (notes) + the recorded summary + `tail` (route)."""
    _cat = args.display
    prep_id = args.prep_id
    on_file = view.on_file
    staged_names = view.suffix
    return (
        f"{lead}## ✅ Recorded {case_count} case(s) for "
        f"**{_cat}**\n\n"
        f"Re-submitting **{_cat}** REPLACES its previous rows "
        "(newest wins).\n\n"
        f"**{on_file}** category row(s) staged for prep_id `{prep_id}`"
        f"{staged_names}.\n\n"
        f"{tail}"
    )


async def _category_reply(
    args: _CategoryArgs, intake: _CategoryIntake, staged: _CategoryStaged
) -> str:
    """Assemble the reply for a category that was recorded."""
    note = await _category_notes(args, intake)
    view = _staged_category_view(staged.rows, intake.meta, staged.on_file)
    note += view.warning
    # F4: tracked SEPARATELY from `note`, which also carries the dropped-cases
    # disclosure -- keying the route wording off `note` would promise a review
    # that never ran whenever cases were dropped.
    # F11: whether a REVIEW IS AVAILABLE at all, independent of whether the
    # host already sent the field. category_dedup_note is empty until the host
    # sends duplicate_groups, which on THIS route it can never do -- so keying
    # the route wording off the note steered testers away from the only route
    # where the review works.
    route = _category_route(review_available=True)
    rows_content = staged.rows.get("content") or []
    # Phase 2 (QA_DUP_SHORTLIST_ENABLED, default OFF): "" unless THIS
    # submission completed the expected set and the prescreen found pairs.
    _shortlist = await _dup_shortlist_note(intake.meta_raw, rows_content)
    # 2026-08-04: say COMPLETE out loud -- see _all_staged_banner.
    _staged_all = _all_staged_banner(intake.meta_raw, rows_content)
    # A4: warn on EVERY category submit from an unrecognised client, not
    # only at finalize -- "" (no-op) whenever this call's own client is a
    # known editor host. Never gates: the row above is already saved.
    _client_warn = _elicit._unrecognized_client_note(
        staged.provenance, context="category"
    )
    return _render_category_reply(
        args,
        len(intake.cases_json),
        view,
        f"{_client_warn}{_staged_all}{note}",
        f"{route}{_shortlist}",
    )


async def handle_submit_category(
    prep_id: str,
    category_name: str,
    suite_json,
    *,
    progress: _common.ProgressCb = None,
    replace_smaller: bool = False,
) -> str:
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
    dispatch_guard.require_dispatched("qa_submit_category")
    # UNTRUSTED: the host-sent id is echoed in replies, so it is sanitised
    # HERE, once, rather than at every interpolation downstream.
    prep_id = safe_prep_id(prep_id)
    args = _category_submit_args(prep_id, category_name)
    if isinstance(args, str):
        return args
    try:
        intake = await _category_intake(args, suite_json, replace_smaller)
        if isinstance(intake, str):
            return intake
        staged = await _stage_category(args, intake)
        if isinstance(staged, str):
            return staged
        return await _category_reply(args, intake, staged)
    except Exception as exc:
        logger.exception("handle_submit_category failed")
        _common._capture_error(exc, "qa_submit_category")
        return f"⚠️ Recording the category failed: {exc}"


async def _staged_row_count(prep_id: str) -> int:
    """How many per-category rows the server ALREADY holds for this prep.

    The refusal WORDING turns on it -- never a verdict. Telling a host to
    "resubmit the COMPLETE suite" when eight categories are already staged buys
    a second full generation pass for nothing and silently rewrites cases the
    tester has already reviewed; ``_staged_resubmit_hint`` carries the measured
    audit.db evidence for that cost.

    Reads defensively: an unreadable store returns 0, which degrades to the
    resend wording rather than claiming work is held when it is not."""
    try:
        rows_res = await prep_store.load_submissions(prep_id)
        rows = (rows_res or {}).get("content") or []
        return sum(
            1
            for r in rows
            if isinstance(r, dict) and str(r.get("category_name") or "").strip()
        )
    except Exception:
        logger.debug("staged row count failed", exc_info=True)
        return 0


async def _staged_names_for_hint(prep_id: str, staged_count: "int | None") -> list:
    """Staged category names (blank placeholders when only a count is known)."""
    if staged_count is not None:
        return [""] * max(0, int(staged_count))
    try:
        rows_res = await prep_store.load_submissions(prep_id)
        rows = (rows_res or {}).get("content") or []
        return [
            str(r.get("category_name") or "").strip()
            for r in rows
            if isinstance(r, dict) and str(r.get("category_name") or "").strip()
        ]
    except Exception:
        logger.debug("staged-rows resubmit hint failed", exc_info=True)
        return []


async def _staged_resubmit_hint(
    prep_id: str,
    extra_field: str = "",
    *,
    field_shape: str = "",
    staged_count: "int | None" = None,
) -> str:
    """How a refused host should come back, given what the server already holds.

    MEASURED (audit.db, prep 2b0de0011a44, TICKET-3541): eight staged categories
    were refused for a missing ambiguity_result, and the host then RE-STAGED all
    eight (two with changed case counts) because it read "resubmit the SAME
    suite" literally. On the staged route the retry carries the missing field
    ALONE and the merge rebuilds from the held rows; ``extra_field`` names that
    top-level key (empty for an ack-only retry).

    ``field_shape`` describes what goes INSIDE that key, so the shape sits next
    to the key it describes and not after "and nothing else".

    ``staged_count`` lets a caller that already counted rows skip a second read.
    ``handle_submit_suite`` reports ZERO on the merged route (``has_full``:
    those rows were not used), and a re-read here would disagree with it.

    Never raises: an unreadable store falls back to the resend wording."""
    staged = await _staged_names_for_hint(prep_id, staged_count)
    if not staged:
        if extra_field:
            return (
                f"resubmit the SAME suite with prep_id `{prep_id}` and a top-level "
                f"`{extra_field}`"
            )
        return f"resubmit the SAME suite with prep_id `{prep_id}`"
    sidecar = (
        '{"' + extra_field + '": ' + (field_shape or "...") + "}"
        if extra_field
        else '"" (empty)'
    )
    return (
        f"**do NOT resend the cases** -- all {len(staged)} staged categor"
        f"{'y is' if len(staged) == 1 else 'ies are'} already held on prep "
        f"`{prep_id}` and the finalize rebuilds the suite from them. Call "
        f"`qa_submit_suite` with prep_id `{prep_id}` and `suite_json={sidecar}` "
        "and nothing else. Regenerating instead costs a second full pass AND "
        "silently changes cases the tester already reviewed"
    )


def _prior_category_count(rows_result: object, category_name: str) -> int:
    """How many cases are ALREADY staged for *category_name* on this prep.

    Reads what ``prep_store.load_submissions`` returned. Returns 0 for an absent
    row, an unreadable payload, or ANY error -- and 0 disables BOTH the duplicate
    disclosure and the shrink guard, so a store hiccup can never refuse a genuine
    submission (the fail-OPEN direction, pinned end to end by a test). Never
    raises."""
    try:
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
    except Exception:
        logger.debug("prior staged category count failed", exc_info=True)
    return 0


def _shrinking_resubmit_reply(
    prep_id: str, category_name: str, prior: int, new: int
) -> str:
    """The GATE reply for a re-submission that would SHRINK a staged category.

    A refusal, NOT a disclosure: nothing was written, so the already-accepted row
    is intact. Never a dead end -- it names the override -- and never a lost
    generation: the cases stay in the caller's own context, so re-sending them
    costs no new generation. 2026-08-04 evidence: on prep 59ab1c49 a `cases: 1`
    row silently replaced a good 12-case one. A plain return, so this function
    cannot break handle_submit_category's never-raise contract."""
    # Bounded, fence-safe display of the caller's own value -- see
    # `_unrecognized_names_line` for the invariant and why there is one
    # helper rather than a fix per site.
    _cat = _reply._clip_echo(category_name, 60)
    return (
        "## \u26d4 Re-submission refused: it would SHRINK "
        f"**{_cat}**\n\n"
        f"**{prior} case(s)** are already staged for this category and this "
        f"submission carries only **{new}**. Accepting it would DELETE "
        f"{prior - new} already-validated case(s) -- the staging write is "
        "replace-by-category, newest wins -- and the usual cause is an output "
        "that was cut short, not a deliberate trim.\n\n"
        "**Nothing was discarded and nothing was saved.** The "
        f"{prior}-case row for **{_cat}** is still staged for prep_id "
        f"`{prep_id}`. Choose ONE:\n\n"
        f"- **Re-send the FULL category** ({prior} or more cases) with "
        "`qa_submit_category` -- do this if the last output was truncated.\n"
        "- **Keep the smaller set on purpose**: call `qa_submit_category` again "
        "with the SAME arguments plus `replace_smaller=true`. That is an "
        "explicit decision to drop the extra case(s).\n\n"
        "> \u2139\ufe0f  If the expected category set is already complete, do "
        "not regenerate anything -- finalize with `qa_submit_suite`.\n\n"
    )


def _resubmit_delta_text(prior: int, new: int) -> str:
    """Three-way case-count phrase (review C2): a shrink must never read "up from"."""
    if new < prior:
        return (
            f"{new} case(s) -- **DOWN from {prior}**; {prior - new} "
            "previously staged case(s) were dropped"
        )
    if new == prior:
        return "the same number of cases"
    return f"{new} case(s), up from {prior}"


def _category_resubmit_note(
    meta: object,
    category_name: str,
    prior: int,
    new: int,
    *,
    overridden: bool = False,
) -> str:
    """Disclose that THIS submission REPLACED an already-staged category row.

    A DISCLOSURE, never a gate: the save has already been decided and this only
    names what happened. "" for a first submit (prior == 0). Never raises -- an
    advisory must not break a submit (same contract as _all_staged_banner).

    TWO different gatings, deliberately (review C1):

    * The ``overridden`` note (a replace_smaller=true call that DID drop
      validated cases) is returned BEFORE the stamp check, so it is
      unconditional: it follows from the GUARD, not the note flag, and gating
      it on the flag made a destructive drop SILENT.
    * The ordinary rework note IS keyed off the prep's META STAMP
      (``host_category_resubmit_note``), never a live flag, so a mid-flow .env
      flip cannot change an in-flight prep and an OLD envelope is unchanged.
    """
    try:
        _cat = _reply._clip_echo(category_name, 60)  # same invariant; see above
        if prior <= 0:
            return ""
        if overridden:
            return _replaced_larger_row_note(_cat, prior, new)
        if not isinstance(meta, dict) or not meta.get("host_category_resubmit_note"):
            return ""
        _delta = _resubmit_delta_text(prior, new)
        return (
            "> \u26a0\ufe0f  **This REPLACED an already-staged row -- it was "
            f"rework.** **{_cat}** was already staged with {prior} "
            f"case(s); this submission carries {_delta}, and is now the stored "
            "version. Re-generating a category that is already staged costs "
            "minutes of chat time and usually changes nothing. Check "
            "`qa_prep_status` before generating a category, and do NOT "
            "re-submit a category unless a reply asked you to.\n\n"
        )
    except Exception:
        logger.debug("category resubmit note failed", exc_info=True)
        return ""


def _replaced_larger_row_note(cat: str, prior: int, new: int) -> str:
    """The note for a replace_smaller=true call that dropped validated cases."""
    return (
        "> ⚠️  **Replaced a larger staged row on purpose.** "
        f"**{cat}** held {prior} case(s) and now holds {new}: "
        f"`replace_smaller=true` was passed, so {prior - new} "
        "already-validated case(s) were DROPPED. Nothing else "
        "changed.\n\n"
    )


def _all_staged_banner(meta: object, rows_content: list) -> str:
    """STOP banner once every expected category has a staged row.

    The 2026-08-03 22:17 live run (prep ea258c02) staged all 8 categories by
    22:23 and then REGENERATED four of them anyway -- ~5 minutes and thousands
    of host-model tokens for zero net change, because no reply ever said the
    set was complete. Returns "" unless meta carries expected_categories and
    every one of them is staged. Advisory only; never raises."""
    try:
        if not isinstance(meta, dict):
            return ""
        expected = [
            str(x) for x in (meta.get("expected_categories") or []) if str(x).strip()
        ]
        if not expected:
            return ""
        staged_names = [
            str(r.get("category_name") or "")
            for r in rows_content
            if isinstance(r, dict)
        ]
        status = prep_status_view(expected=expected, staged_raw_names=staged_names)
        if not status.get("ready"):
            return ""
        n = len(expected)
        return (
            f"## \U0001f3c1 All {n}/{n} expected categories are staged -- "
            "STOP generating\n\n"
            "**Do NOT regenerate, rewrite, or re-submit any category.** The set "
            "is complete; a re-submission only replaces near-identical rows and "
            "costs minutes of chat time. The single remaining step is to "
            "finalize: call `qa_submit_suite` with this prep_id now, using ONE "
            "of the routes described below.\n\n"
        )
    except Exception:
        logger.debug("all-staged banner failed", exc_info=True)
        return ""


async def _dup_shortlist_note(meta: object, rows_content: list) -> str:
    """Server-assisted duplicate shortlist (QA_DUP_SHORTLIST_ENABLED, OFF).

    When THIS submission completed the expected category set, merge the
    staged rows (pure -- nothing is persisted or deleted) and append
    lexically prescreened candidate duplicate pairs, printed with POST-MERGE
    GLOBAL tc_ids so the host confirms a shortlist via the finalize sidecar
    instead of re-reading the merged suite. Needs
    QA_HOST_DEDUP_REVIEW_ENABLED (it feeds that review). Returns "" when
    gated off, not ready, no pairs, or on ANY error -- it must never break or
    block a category submit. Deliberate F4/F11 carve-out, stated here so it
    reads as a decision: this section names `duplicate_groups` in a
    qa_submit_category reply, but only under the OFF-by-default flag and only
    as the server's OWN prescreen output (a review the server just ran), not
    as a request to attach the field to a per-category submission."""
    try:
        if not dup_shortlist_on():
            return ""
        if not isinstance(meta, dict):
            return ""
        expected = list(meta.get("expected_categories") or [])
        if not expected:
            return ""
        rows = rows_content
        staged_names = [
            str(r.get("category_name") or "") for r in rows if isinstance(r, dict)
        ]
        status = prep_status_view(expected=expected, staged_raw_names=staged_names)
        if not status.get("ready"):
            return ""
        merged_dict, _used, _id_map = prep_status._merge_category_rows(rows)
        pairs = build_dup_shortlist(merged_dict.get("test_cases") or [])
        return build_dup_shortlist_section(pairs)
    except Exception:
        logger.debug("dup shortlist prescreen failed", exc_info=True)
        return ""


def _oversized_submission_note(obj: object) -> str:
    """``""`` if ``obj`` is within ``QA_PREP_MAX_BYTES``, else a refusal naming it.

    The F7 fix stopped applying the cap to the SERVER's own merge of already-
    measured category rows, which is right: eight accepted categories could
    never finalize, and the reply blamed the tester for JSON they never sent.
    But the Path-A sidecar branch overlays FIVE fields taken from the CURRENT
    call's `suite_json` -- duplicate_groups, acceptance_criteria,
    ambiguity_result, checklist_items, image_descriptions -- into that merge
    before parsing it, and `parse_host_suite`'s cap was the only thing measuring
    them. With the cap off for the whole call, a 12 MB `acceptance_criteria` was
    accepted at the default 4 MB limit (measured in review).

    So the bound is applied HERE, to what the TESTER sent, before the overlay --
    which is what F7 said it should bound all along. Never raises: a
    non-serialisable object is not oversized, and `parse_host_suite` refuses it
    by name a few lines later.
    """
    cap = int(getattr(settings, "qa_prep_max_bytes", 0) or 0)
    if not cap or obj is None:
        return ""
    try:
        size = len(json.dumps(obj, ensure_ascii=False).encode("utf-8", "ignore"))
    except (TypeError, ValueError, RecursionError):
        return ""
    if size <= cap:
        return ""
    return (
        f"\u26a0\ufe0f The review fields in this submission are {size} bytes, over "
        f"the {cap}-byte limit. Your per-category test cases are still staged and "
        "are NOT affected -- resend this submit with a smaller "
        "`duplicate_groups` / `acceptance_criteria` / `ambiguity_result` / "
        "`checklist_items` / `image_descriptions`, or with none of them, and the "
        "suite will finalize from the staged rows."
    )


async def _mark_assertion_refused(prep_id: str, envelope: dict, meta: dict) -> None:
    """Two-beat ack: stamp the prep so a LATER step_assertion_ack is honoured.
    Non-fatal: an unpersisted mark only means the next ack is refused again."""
    try:
        _mark = await prep_store.update_prep(
            prep_id,
            {**envelope, "meta": {**meta, "assertion_refused": True}},
        )
        if (_mark or {}).get("error"):
            logger.warning(
                "prep %s not marked assertion_refused (%s)",
                prep_id,
                (_mark or {}).get("error"),
            )
    except Exception:  # pragma: no cover - must never block the refusal
        logger.debug("assertion_refused mark failed", exc_info=True)


async def _volume_floor_gate(
    prep_id: str, envelope: dict, all_cases: list, staged_rows: int, ack: bool
) -> tuple[str | None, str]:
    """The generation-VOLUME gate, run on BOTH finalize routes.

    Returns ``(refusal, note)``: the refusal text (the caller prefixes its
    version note) or None to carry on, and the reply disclosure. A refusal
    destroys nothing; volume is measured pre-dedup."""
    meta = envelope.get("meta") or {}
    _vmode, _vmd = prep_status._volume_floor_note(
        meta, all_cases, prep_id, ack=bool(ack), staged=staged_rows
    )
    if _vmode == "refuse":
        # TWO-BEAT ack: mark the prep refused so a LATER volume_floor_ack is
        # honoured. Non-fatal: an unpersisted mark fails safe. 2026-08-12: the
        # count is stamped so the NEXT refusal can say the resubmission shrank.
        try:
            _mark = await prep_store.update_prep(
                prep_id,
                {
                    **envelope,
                    "meta": {
                        **meta,
                        "volume_refused": True,
                        "volume_last_count": len(all_cases),
                    },
                },
            )
            if (_mark or {}).get("error"):
                logger.warning(
                    "prep %s not marked volume_refused (%s)",
                    prep_id,
                    (_mark or {}).get("error"),
                )
        except Exception:  # pragma: no cover - must never block the refusal
            logger.debug("volume_refused mark failed", exc_info=True)
        await _common._audit(
            "mcp_submit_suite_refused",
            entity_id=prep_id,
            detail={"reason": "volume_floor", "submitted_cases": len(all_cases)},
        )
        return _vmd, ""
    if _vmode == "acked":
        await _common._audit(
            "mcp_submit_suite_volume_override",
            entity_id=prep_id,
            detail={"reason": "volume_floor_ack", "submitted_cases": len(all_cases)},
        )
    return None, (_vmd if _vmode else "")


async def _assertion_ack_shape(prep_id: str) -> str:
    """How the tester finalizes as-is: with rows staged the ack alone is the
    whole retry; otherwise nothing extra is said."""
    rows = ((await prep_store.load_submissions(prep_id)) or {}).get("content") or []
    if not rows:
        return ""
    return (
        " and "
        + export._finalize_route_phrase(short=True)
        + " -- the staged categories are held server-side, so do not resend them"
    )


def _assertion_refusal_text(
    prep_id: str, over: list, findings: list, first_beat: str, ack_shape: str
) -> str:
    """The step-assertion refusal body (the caller prefixes its version note)."""
    detail = "\n".join(
        f"- **{name}**: {hit} of {total} step(s) ({ratio:.0%})"
        for name, hit, total, ratio in over
    )
    return (
        "⛔ **Submission refused:** in the "
        "categor(ies) below, most steps have an `expected_result` "
        "that asserts nothing the `action` did not already say -- "
        "typically a restatement of the action itself. A step like "
        "that passes against correct software AND against broken "
        "software, so executing it measures nothing.\n\n"
        f"{detail}\n\n"
        f"{step_assertion.step_assertion_detail(findings)}\n\n"
        "Rewrite those expected results to name the concrete "
        "observable outcome -- the exact on-screen message, the "
        "field/button state, or the resulting screen -- and resubmit "
        f"under the SAME prep_id `{prep_id}`. **Nothing was "
        "discarded**: the prep and every staged category row "
        "survive. To finalize as-is "
        "anyway, ask the tester first and then resend with "
        f"`step_assertion_ack=true`{ack_shape}.{first_beat}"
    )


async def _step_assertion_gate(
    prep_id: str, envelope: dict, all_cases: list, findings: list, ack: bool
) -> tuple[str | None, str]:
    """F4 (2026-08-29): the ENFORCEMENT gate against steps whose expected_result
    merely restates the action. Same ``(refusal, note)`` contract as
    ``_volume_floor_gate``; placed beside it and before finalize for the same
    reason: a refusal costs one round trip and destroys nothing."""
    over = step_assertion.categories_over_threshold(all_cases)
    if not over:
        return None, ""
    meta = envelope.get("meta") or {}
    refused_before = bool(meta.get("assertion_refused"))
    categories = [row[0] for row in over]
    detail = {"categories": categories, "flagged_steps": len(findings)}
    if ack and refused_before:
        await _common._audit(
            "mcp_submit_suite_assertion_override",
            entity_id=prep_id,
            detail={"reason": "step_assertion_ack", **detail},
        )
        return None, (
            "> ⚠️  Finalized with `step_assertion_ack=true` over "
            f"{len(findings)} step(s) whose expected_result asserts "
            "nothing the action did not already state.\n\n"
        )
    first_beat = ""
    if ack:
        first_beat = (
            "\n\n> ⚠️  `step_assertion_ack=true` arrived on the "
            "FIRST submit for this prep and was IGNORED -- the finding "
            "below had not been shown to anyone yet."
        )
    await _mark_assertion_refused(prep_id, envelope, meta)
    await _common._audit(
        "mcp_submit_suite_refused",
        entity_id=prep_id,
        detail={"reason": "step_assertion", **detail},
    )
    ack_shape = await _assertion_ack_shape(prep_id)
    return _assertion_refusal_text(prep_id, over, findings, first_beat, ack_shape), ""


def _skipped_attachments_gen_notes(meta: dict) -> list:
    """Generation note: attachments the tester's own text-only pick skipped."""
    try:
        note = generate_prepare._text_only_image_skip_note(meta)
        return [("Skipped attachments", note)] if note else []
    except Exception:
        logger.debug("skipped-attachments gen note failed", exc_info=True)
        return []


async def _recent_suite_gen_notes(source_text: str) -> list:
    """Generation note: a suite for this same source was saved recently."""
    try:
        note = await generate._recent_suite_warning_note(
            source_text, generate._RECENT_SUITE_WARN_WINDOW_S
        )
        return [("Recent suite for this source", note)] if note else []
    except Exception:
        logger.debug("recent-suite gen note failed", exc_info=True)
        return []


async def _early_gen_notes(
    meta: dict, dup_note: str, source_text: str, provenance: dict, suite: object
) -> list:
    """A1 (2026-09-26): signals that already exist server-side but never
    reached the Generation Notes sheet, up to the volume shortfall. Each is
    independent and best-effort; the order is the sheet's order."""
    notes = _skipped_attachments_gen_notes(meta)
    if dup_note:
        notes.append(("Duplicate cases", dup_note))
    notes += await _recent_suite_gen_notes(source_text)
    provenance_detail = _elicit._provenance_gen_note_detail(provenance)
    if provenance_detail:
        notes.append(("Session provenance", provenance_detail))
    screen_ref = str(meta.get("image_reference_advisory") or "")
    if screen_ref:
        notes.append(("Screen references without attachments", screen_ref))
    vol_detail = prep_status._volume_shortfall_detail(
        meta, list(getattr(suite, "test_cases", None) or [])
    )
    if vol_detail:
        notes.append(("Under-generated categories", vol_detail))
    return notes


def _checklist_gap_gen_notes(checklist_gap: bool) -> list:
    """Generation note: the run returned no usable requirements checklist."""
    if not checklist_gap:
        return []
    return [
        (
            "No requirements checklist",
            "The requirement decomposition runs in the tester's chat "
            "model, not on this server, and this run returned no "
            "usable `checklist_items`. There is therefore NO "
            "requirements checklist for this suite, and no "
            "Requirements Checklist sheet. "
            "Nothing was invented to fill the gap -- treat the "
            "suite's coverage as unmeasured, not as complete.",
        )
    ]


def _ambiguity_gen_notes(amb_result: object) -> list:
    """Generation notes from the ambiguity preflight: it did not run, it rated
    the source medium/high (element names are inferred), and any unreadable
    field recorded in ``result.notes`` (the chat reply is transient; the
    workbook is what the tester keeps)."""
    notes: list = []
    ran = getattr(amb_result, "ran", False)
    if amb_result is not None and not ran:
        notes.append(
            (
                "Ambiguity preflight did not run",
                "The testability pre-pass was "
                "declared `blocking: true` in the prepare payload, but "
                "it runs inside the tester's chat model and this "
                "submission came back with no readable result -- so "
                "there is no evidence it ran, and this server cannot "
                "enforce a step it does not execute. These cases were "
                "generated against a ticket nothing checked for being "
                "too under-specified to test. That is NOT the same as "
                "'checked and found nothing'.",
            )
        )
    if amb_result is not None and ran:
        if getattr(amb_result, "severity", "") in ("medium", "high"):
            notes.append(
                (
                    "UI element names may be inferred",
                    "The testability preflight rated this source "
                    f"`{amb_result.severity}`. Where the source names no "
                    "screen, no control and no label, the screen names, "
                    "element names and UI copy in these cases were "
                    "inferred by the generating model, not promised by "
                    "the ticket. Confirm an element exists before "
                    "raising a defect for its absence, and treat a "
                    "mismatch in wording as a question for the BA rather "
                    "than a bug.",
                )
            )
    amb_note = ambiguity_notes_gen_note(amb_result)
    if amb_note is not None:
        notes.append(amb_note)
    return notes


def _step_assertion_gen_notes(findings: list, data_findings: list) -> list:
    """F4 (2026-08-29): advisory notes in the workbook the tester keeps. The
    REFUSAL is the gate; the test_data finding never gates at all."""
    notes: list = []
    assert_detail = step_assertion.step_assertion_detail(findings)
    if assert_detail:
        notes.append(("Steps that cannot fail", assert_detail))
    data_detail = step_assertion.test_data_detail(data_findings)
    if data_detail:
        notes.append(("Test data that restates the field name", data_detail))
    return notes


def _quality_advisory_gen_notes(
    all_cases: list, source_text: str, summary: str
) -> list:
    """C1 (2026-09-26) suite-quality advisories, then F8 (2026-08-30) the FULL
    generation advisory when the chat reply cut it at ``_SUMMARY_CAP``. Both
    are best-effort: a note must never cost the tester the export."""
    notes: list = []
    try:
        for title, detail in compute_suite_quality_advisories(
            all_cases, staged_text=source_text
        ):
            notes.append((title, detail))
    except Exception:  # pragma: no cover - advisory never blocks export
        logger.debug("compute_suite_quality_advisories failed", exc_info=True)
    try:
        full_summary = (summary or "").strip()
        if len(full_summary) > _reply._SUMMARY_CAP:
            notes.append(
                ("Full generation advisory (the chat reply was cut)", full_summary)
            )
    except Exception:  # pragma: no cover - a note never blocks the export
        logger.debug("full-advisory note attachment failed", exc_info=True)
    return notes
