"""Case-quality gate of qa_submit_suite (moved from handlers, C3a step 3a).

C2 (2026-08-30 audit): the submit-time CASE QUALITY gate. It reads the FINAL
merged+renumbered suite rather than the submitted cases, because the
duplicate-title signal only means anything after the merge and the tc_ids it
prints must be the ids the tester reads in the export. It runs after
finalize, BEFORE the persist, the export and the finalized stamp, so a
refusal costs one round trip and destroys nothing: the prep and every staged
category row survive and nothing is written.
Two-beat, exactly like volume_floor_ack / step_assertion_ack: an ack on the
FIRST submit is IGNORED and told so, because the tester cannot have seen
these findings yet. A CLEAN suite yields no section and no refusal, so its
reply stays byte-identical.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from agents.host_submit_ports_prep import SubmitPorts
from agents.host_submit_types import SubAcks, SubPrep, SubQuality
from config.settings_guard import never_raise
from tools import case_quality_gate, prep_store

logger = logging.getLogger("agents.host_mode")

_FIRST_BEAT = (
    "\n\n> ⚠️  `quality_gate_ack=true` arrived on "
    "the FIRST submit for this prep and was IGNORED -- the "
    "findings below had not been shown to anyone yet."
)


class GateIn(NamedTuple):
    prep: SubPrep
    suite: Any
    acks: SubAcks


class RefusalIn(NamedTuple):
    prep: SubPrep
    quality: Any
    kinds: list
    first_beat: str


async def mark_refused(prep: SubPrep) -> None:
    prep_id = prep.prep_id
    envelope = prep.envelope
    meta = prep.meta
    # pragma: no cover - must never block a refusal
    with never_raise(logger, "quality_refused mark failed", exc_info=True):
        _mark = await prep_store.update_prep(
            prep_id,
            {**envelope, "meta": {**meta, "quality_refused": True}},
        )
        if (_mark or {}).get("error"):
            logger.warning(
                "prep %s not marked quality_refused (%s)",
                prep_id,
                (_mark or {}).get("error"),
            )


def _refusal_text(args: RefusalIn) -> str:
    prep_id = args.prep.prep_id
    return (
        f"{args.prep.version_note}⛔ **Submission refused:** the finished "
        "suite carries cases a tester cannot execute as written.\n\n"
        f"{case_quality_gate.gate_detail(args.quality)}\n\n"
        "Fix those cases -- give every step concrete `test_data`, "
        "make each `expected_result` name the observable outcome "
        "instead of repeating the action, and give duplicated titles "
        "distinct behaviour -- then resubmit under the SAME prep_id "
        f"`{prep_id}`. **Nothing was discarded**: the prep and every "
        "staged category row survive, and nothing was exported or "
        "saved. To finalize as-is anyway, "
        "ask the tester first and then resend with "
        f"`quality_gate_ack=true`.{args.first_beat}"
    )


async def refusal(args: RefusalIn, ports: SubmitPorts) -> str:
    await mark_refused(args.prep)
    await ports.common.audit(
        "mcp_submit_suite_refused",
        entity_id=args.prep.prep_id,
        detail={
            "reason": "case_quality",
            "findings": len(args.quality.findings),
            "kinds": args.kinds,
        },
    )
    return _refusal_text(args)


async def _override(args: RefusalIn, ports: SubmitPorts) -> str:
    await ports.common.audit(
        "mcp_submit_suite_quality_override",
        entity_id=args.prep.prep_id,
        detail={
            "reason": "quality_gate_ack",
            "findings": len(args.quality.findings),
            "kinds": args.kinds,
        },
    )
    return (
        "> ⚠️  Finalized with `quality_gate_ack=true` over "
        f"{len(args.quality.findings)} case-quality finding(s) "
        f"({', '.join(args.kinds)}). Those cases are in the suite and in "
        "the export AS SUBMITTED.\n\n"
    )


async def gate(args: GateIn, ports: SubmitPorts) -> str | SubQuality:
    cases = list(getattr(args.suite, "test_cases", None) or [])
    quality = case_quality_gate.scan_suite(cases)
    if not quality.found:
        return SubQuality("")
    kinds = sorted(quality.counts)
    ack = args.acks.quality_gate
    refused_before = bool(args.prep.meta.get("quality_refused"))
    if not (ack and refused_before):
        beat = _FIRST_BEAT if ack and not refused_before else ""
        return await refusal(RefusalIn(args.prep, quality, kinds, beat), ports)
    return SubQuality(await _override(RefusalIn(args.prep, quality, kinds, ""), ports))
