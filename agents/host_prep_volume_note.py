"""The volume-floor gate: one verdict note per finalizing suite, plus the workbook cell."""

from __future__ import annotations

import logging

from agents.host_prep_volume import (
    VolumeCheck,
    bucket_cases_by_category,
    volume_contract,
    volume_verdict,
)
from agents.host_prep_volume_refusal import (
    volume_facts,
    volume_refusal_text,
    volume_warning_text,
)
from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")

# WHY prep_volume_floor_note EXISTS: every ``categories[]`` entry and every job
# packet of the prepare payload carries ``min_cases``
# (host_mode.build_prepare_payload), and nothing on the submit side ever
# checked it. On 2026-08-09 08:23 a host ignored the fan-out contract, produced
# ONE case per category inline in the parent turn and finalized a merged 8-case
# suite 28 seconds after prepare; it was accepted in silence and exported as if
# normal, against 99 and 97 cases (never fewer than 12 per category) on two
# comparable runs.
#
# ONE decision point for BOTH halves of that failure -- the volume floor, and
# the fan-out completeness check that fanout_incomplete_note only ever applied
# to the two STAGED routes ("Path B (non-empty suite_json) is unaffected", as
# its call site put it). A prep that stamped ``parallel_fanout`` and finalizes
# a merged blob missing a whole expected category never honoured the
# orchestration contract either.
#
# IT RUNS ON BOTH ROUTES. Path A -- ``qa_submit_category`` x N then an empty
# ``suite_json`` -- is what ``build_orchestration`` marks ``preferred``, so
# gating only the merged route would have left the recommended one as a free
# bypass: 8 staged rows of 1 case each pass fanout_incomplete_note (complete,
# not sufficient) and ship the same 8-case suite. Bucketing is in fact MORE
# reliable there, because merge_category_rows stamps each case's ``category``
# from the SERVER-DERIVED ``category_name`` of the tool call.
#
# R1: this binds a PARTIAL staged set even when the prep never requested the
# fan-out. handle_submit_category also serves "a weaker host that submits
# incrementally", and for such a prep fanout_incomplete_note returns "" for
# ANY subset, so staging 3 of 8 categories used to finalize silently. The
# volume contract applies regardless of route: 3 of 8 is under-generation
# however the cases arrived. Only the EMPTY-category refusal is
# fan-out-specific, because only that prep was promised one worker per
# category.
#
# PER-CATEGORY DERIVATION ON PATH B: a merged case carries its OWN ``category``
# (``category_source: "host"``). This runs AFTER the has_full normalisation
# loop, so every value has already been through ``normalize_category``; it
# re-normalises defensively (idempotent) and buckets anything MISSING or
# UNRECOGNISED into ``unknown``. Unknown cases count toward the TOTAL (they are
# real cases) but toward no category, and they are NAMED in the note. They
# downgrade an empty-category refusal only when there are at least ``floor`` of
# them -- enough to plausibly BE the missing category. One deleted ``category``
# field must not disarm the gate, while a suite of 100 unlabelled cases must
# not be refused for a labelling defect; that is a PROPORTIONAL test, not an
# on/off one.
#
# Every decision reads this prep's META STAMPS (``volume_floor``,
# ``volume_min_cases``, ``volume_categories``, ``parallel_fanout``,
# ``volume_refused``), never a live settings flag, so neither a mid-flow .env
# flip nor a launcher auto-update between prepare and submit can change an
# in-flight prep. An envelope written before those stamps existed returns
# ``("", "")`` on the first guard -- inert, never a spurious refusal.
#
# E02 (2026-08-20) -- REDISTRIBUTION IS LEGITIMATE. The per-category floor used
# to be reported whenever a category came in under it, even when the suite as a
# whole honoured the contract. That was consistent with the prompt of the day,
# which asked every category for the same number and got it: 3/3 measured runs
# returned an identical count across all eight workers (12, 12, 13; zero
# within-run disagreement), because the instruction scoped the COUNT to the
# category and its CONDITION to the feature. Now that the prompt asks each
# worker to judge its OWN category's material, a thin category is a correct
# answer, and warning about it would make the honest outcome look like a
# shortfall on every run.
#
# So the TOTAL is the contract -- it is what the 2026-08-09 collapse actually
# violated (8 cases, one per category) -- and per category only two things are
# still reported when the total is met: a category that is EMPTY, and one that
# has COLLAPSED to less than half its floor. Half is ``_VOLUME_REFUSE_RATIO``,
# reused rather than invented: it is the same "materially short" line already
# drawn for the total. It is NOT measured on this axis and cannot be until
# suites generated under the new prompt exist, because no run before it was
# free to vary per category -- the two known-good 2026-08-04 runs bottom out at
# exactly the floor. Calibrate it against the first such suites; do not read it
# as evidence-backed today.
#
# Contract of volume_floor_note(check) -> (mode, text):
#
# Returns ``("", "")`` when this prep carries no volume contract or the suite
# honours it, ``("warn", note)`` for a soft shortfall, ``("acked", note)``
# when a refusal was overridden by ``volume_floor_ack`` on a prep this gate
# had ALREADY refused, and ``("refuse", markdown)`` when the suite is
# materially below the floor THIS prep's own payload demanded.
#
# ``check.staged`` is how many per-category rows the server ALREADY holds for
# this prep (0 on the merged route, where they were deliberately not used). It
# reorders the refusal's options ONLY -- no verdict turns on it; see
# ``_staged_resubmit_hint``. ``post_dedup=True`` is the WARNING-ONLY channel
# used once the suite is finalized: it returns a warning where it would
# otherwise refuse, and nothing at all otherwise.
#
# Never raises; on an unexpected error it fails OPEN (returns ``("", "")``)
# so a legitimate finalize is never blocked by the guard itself -- the same
# discipline as fanout_incomplete_note.


def volume_floor_note(check: VolumeCheck) -> "tuple[str, str]":
    """Verdict on the generation VOLUME of a finalizing suite (see the block above)."""
    # pragma: no cover - defensive, must never block a finalize
    with never_raise(logger, "volume floor gate failed", exc_info=True):
        verdict = volume_verdict(check)
        if verdict is None:
            return "", ""
        facts = volume_facts(verdict, check.meta, check.post_dedup)  # type: ignore[arg-type]
        if verdict.mode == "refuse":
            return verdict.mode, volume_refusal_text(verdict, facts, check)
        return verdict.mode, volume_warning_text(verdict, facts, check)
    return "", ""


def _shortfall_line(short: list, names: list, floor: int) -> str:
    """The plain-text sentence naming every short category."""
    listed = ", ".join(f"{n} {got}/{floor}" for n, got in short)
    return (
        f"{len(short)} of {len(names)} categories came back below the "
        f"{floor} cases per category this prep asked for: {listed}. "
        "The suite was accepted and exported anyway -- nothing blocked it. "
        "Re-generate the short categories if that was not deliberate."
    )


def _shortfall_text(meta: object, cases: list) -> str:
    contract = volume_contract(meta)
    if contract is None:
        return ""
    floor, names = contract
    counts = bucket_cases_by_category(cases)[0]
    short = [(n, counts.get(n, 0)) for n in names if counts.get(n, 0) < floor]
    if not short:
        return ""
    return _shortfall_line(short, names, floor)


def volume_shortfall_detail(meta: object, cases: list) -> str:
    """One plain-text line naming every category below this prep's floor, or "".

    The workbook cell version of what volume_floor_note renders as markdown.
    Kept SEPARATE rather than parsed out of that note, because the note is
    tester-facing prose that is rewritten whenever the wording is improved and a
    cell should not break when a sentence changes.

    It MIRRORS the gate's bucketing (unlabelled cases count toward the total but
    toward no category) and applies NO slack: this is a record, not a verdict,
    so `7/8` belongs in it even though 7/8 is deliberately not worth a warning.
    Never raises.
    """
    # pragma: no cover - defensive; a note must never raise
    with never_raise(logger, "volume shortfall detail failed", exc_info=True):
        return _shortfall_text(meta, cases)
    return ""
