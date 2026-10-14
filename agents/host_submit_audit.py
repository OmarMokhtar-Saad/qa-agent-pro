"""Audit trail of qa_submit_suite (moved from handlers, C3a step 3b).

Review counts are recorded ONLY when the job actually ran, so a flag-OFF
run's audit row is byte-identical to the pre-feature one.

``host_ambiguity_ran`` / ``host_ambiguity_disclosed``: "absent" alone made a
FORFEITED safety gate read identically to "checked, found nothing" (row 99,
2026-08-09), so the row records whether the preflight verdict was READABLE and
whether the tester was actually TOLD. ``host_image_disclosed`` means the
tester was WARNED in either form (off-topic block or "no usable verdict").

``cases`` is the POST-removal count; ``dedup_groups`` counts what was SUBMITTED
(before the safety screen), ``dedup_removed`` what was actually deleted, and
``dedup_offered`` whether the field was OFFERED at all.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from agents.host_submit_ports_prep import SubmitPorts
from agents.host_submit_types import SubFinal, SubRun, SubSaved
from tools import telemetry


class RecordIn(NamedTuple):
    run: SubRun
    fin: SubFinal
    saved: SubSaved


class AuditIn(NamedTuple):
    run: SubRun
    status: Any
    case_count: int
    suite: Any


def _ac_detail(run: SubRun) -> dict:
    ac_result = run.ac.result
    if ac_result is None:
        return {}
    return {"host_acs": len(ac_result.acs), "host_acs_dropped": ac_result.dropped}


def _amb_detail(run: SubRun) -> dict:
    amb_result = run.amb.result
    if amb_result is None:
        return {}
    return {
        "host_ambiguity_severity": (amb_result.severity or "absent"),
        "host_ambiguity_ran": bool(amb_result.ran),
        "host_ambiguity_disclosed": bool(run.amb.note),
    }


def _image_detail(run: SubRun) -> dict:
    img_note = run.images.note
    _img_result = run.images.result
    _img_counts = run.images.counts
    if _img_result is None or not run.prep.meta.get("host_image_relevance"):
        return {}
    return {
        "host_image_relevance_ran": bool(_img_counts.get("ran")),
        "host_image_off_topic": int(_img_counts.get("no") or 0),
        "host_image_disclosed": bool(
            img_note
            and (getattr(_img_result, "off_topic", None) or not _img_counts.get("ran"))
        ),
    }


def audit_review_detail(run: SubRun) -> dict:
    return {**_ac_detail(run), **_amb_detail(run), **_image_detail(run)}


def submit_audit_detail(args: AuditIn, ports: SubmitPorts) -> dict:
    run = args.run
    parsed = run.read.parsed
    return {
        "status": args.status,
        "cases": args.case_count,
        "dedup_groups": run.dup.groups_submitted,
        "dedup_removed": len(run.dup.removed),
        **audit_review_detail(run),
        **ports.prep_gate.rtm_trace_detail(args.suite),
        "dedup_offered": bool(getattr(parsed, "duplicate_review_offered", False)),
    }


async def record_submit(args: RecordIn, ports: SubmitPorts) -> None:
    run, fin = args.run, args.fin
    suite = fin.suite
    suite_id = args.saved.suite_id
    case_count = len(getattr(suite, "test_cases", []) or [])
    telemetry.add_tool_properties(case_count=case_count, source="host")
    await ports.common.audit(
        "mcp_submit_suite",
        entity_id=suite_id or None,
        detail=submit_audit_detail(AuditIn(run, fin.status, case_count, suite), ports),
    )
