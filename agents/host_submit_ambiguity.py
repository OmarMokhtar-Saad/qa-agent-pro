"""Ambiguity-preflight gate of qa_submit_suite (moved from handlers, C3a step 2a).

The ambiguity job's verdict. QA_HOST_AMBIGUITY_REVIEW_ENABLED removed this
server's classifier call AND every signal that the blocking preflight ran at
all, so submit accepted a suite identically whether the host obeyed step 0 or
skipped it. Keyed off the prep's meta stamp (not the live flag) so a mid-flow
.env flip cannot change an in-flight prep. UNTRUSTED and NOT a permission bit:
a host that lies "none" is not stopped here; what it buys is that "no verdict"
stops looking like "cleared". ``getattr`` on settings, not direct access, so a
partial rollback of the settings-field edit is not a bare AttributeError.

The prep is deliberately NOT deleted on refusal: the tester can run the
preflight and resubmit with the same prep_id. ``staged_rows`` is passed, not
re-read. F4 (2026-08-30): a ``high`` verdict never clears (no ack argument, no
override), so its refusal says what to DO about it instead of looping on
"run step 0 and resubmit".
"""

from __future__ import annotations

from typing import Any, NamedTuple

from agents.host_category_calls import StagedHintIn
from agents.host_submit_ports_prep import SubmitPorts
from agents.host_submit_types import SubAmb


class AmbRefusalIn(NamedTuple):
    prep_id: str
    amb_result: Any
    amb_note: str
    staged_rows: int


class AmbIn(NamedTuple):
    prep_id: str
    meta: dict
    parsed: Any
    staged_rows: int


def _high_text(amb_note: str, prep_id: str) -> str:
    return (
        f"{amb_note}⛔ **Submission refused:** your own "
        "ambiguity preflight rated this source **high** — too "
        "under-specified to write trustworthy test cases from. "
        "Re-running step 0 will not change that, and there is no "
        "acknowledgement argument that overrides it.\n\n"
        "**Do this instead:** put the unanswered questions above "
        "to the tester, get the ticket or the description "
        "amended, then call `qa_prepare_test_cases` again with "
        "the fuller source. An operator who accepts the risk can "
        "set `QA_HOST_AMBIGUITY_REQUIRE_RESULT=false`, which "
        "discloses the verdict and returns the suite instead of "
        "refusing it. Nothing was discarded — prep_id "
        f"`{prep_id}` still holds this work."
    )


def _missing_text(amb_note: str, how: str) -> str:
    return (
        f"{amb_note}⛔ **Submission refused:** `QA_HOST_AMBIGUITY_REQUIRE_RESULT` is on and this submission "
        "carries no cleared ambiguity preflight. Run step 0 of "
        f"the payload's `jobs_to_run`, then {how}. "
        "Nothing was discarded."
    )


async def refusal(args: AmbRefusalIn, ports: SubmitPorts) -> str:
    await ports.common.audit(
        "mcp_submit_suite_refused",
        entity_id=args.prep_id,
        detail={
            "reason": "ambiguity_result",
            "severity": args.amb_result.severity or "absent",
        },
    )
    how = await ports.category.staged_resubmit_hint(
        StagedHintIn(args.prep_id, "ambiguity_result", staged_count=args.staged_rows)
    )
    if args.amb_result.ran and args.amb_result.severity == "high":
        return _high_text(args.amb_note, args.prep_id)
    return _missing_text(args.amb_note, how)


async def review(args: AmbIn, ports: SubmitPorts) -> str | SubAmb:
    amb_result = None
    amb_note = ""
    if args.meta.get("host_ambiguity_review"):
        raw = getattr(args.parsed, "raw_ambiguity_result", None)
        amb_result = ports.review.extract_ambiguity_result(raw)
        amb_note = ports.review.build_ambiguity_result_section(amb_result)
        if (
            getattr(ports.io.settings, "qa_host_ambiguity_require_result", False)
            and not amb_result.cleared
        ):
            req = AmbRefusalIn(args.prep_id, amb_result, amb_note, args.staged_rows)
            return await refusal(req, ports)
    return SubAmb(amb_result, amb_note)
