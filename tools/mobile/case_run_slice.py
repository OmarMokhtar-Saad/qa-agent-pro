"""Taking the logcat slice after a replay and recording it on the case."""

from __future__ import annotations

import logging

from tools.mobile.case_run_evidence import (
    _finish_wire,
    _prior_content,
    _splice_evidence,
)
from tools.mobile.case_run_record import _evidence_record, _merge_crash
from tools.mobile.case_run_types import _Ref
from tools.mobile.replay.context import Context
from tools.mobile_evidence import capture

# Same object as case_run_consts.logger (getLogger is a registry); a local
# binding lets ruff see the handler below as logged (BLE001).
logger = logging.getLogger("tools.mobile.case_runner")


def _record_slice(evidence: dict, sliced: object) -> None:
    """Append one log slice to the evidence record and merge its crash in."""
    content = sliced.get("content") if isinstance(sliced, dict) else None
    content = content if isinstance(content, dict) else {}
    evidence["slices"] += 1
    this_crash = content.get("crash")
    this_crash = this_crash if isinstance(this_crash, dict) else {}
    evidence["slices_written"].append(
        {
            "index": evidence["slices"] - 1,
            "path": str(content.get("path") or ""),
            "lines": content.get("lines"),
            "truncated": bool(content.get("truncated")),
            "skipped": content.get("skipped")
            or (sliced.get("error") if isinstance(sliced, dict) else None),
            "crash": this_crash,
        }
    )
    evidence["crash"] = _merge_crash(evidence.get("crash"), this_crash)


async def _take_slice(ref: _Ref, evidence: dict) -> None:
    """Pull the slice from ``capture`` and record it on *evidence*."""
    ctx = ref.ctx
    # H2: the typed values must ALSO be known at run end, when the app's own
    # event log is pulled; remembered in memory only, never on disk.
    capture.remember_typed(ref.run_id, (ctx.tester_inputs or {}).values())
    sliced = await capture.slice_case(
        capture.CaseRef(ctx.serial, ctx.package, ref.run_id, ref.tc_id),
        evidence["slices"],
        capture.SliceCtx(evidence, ctx.tester_inputs),
    )
    _record_slice(evidence, sliced)


# Take this replay's logcat slice, record it, and return the case's crash.
#
# Reads the record ``start_case`` left, asks ``capture`` for the slice --
# which redacts the tester's typed values BEFORE anything reaches disk (plan
# D4) and judges the scrubbed text for a fatal event -- and writes the updated
# record back so the checkpoint that follows carries it forward. A skipped or
# failed slice is stored as such.
#
# **The STORING can never change or block a verdict**, which is why every
# exception still ends here. The returned value is a separate thing: the crash
# record for the CASE (first crash wins, ``_merge_crash``), which the caller
# hands to ``_crash_override`` one branch later. On any failure at all -- a
# detector that raised, a slice that was never taken, a store that would not
# read -- this returns ``{}``, i.e. "no crash proven", so an evidence fault
# leaves the model's verdict exactly as it was.
async def _slice_evidence(run_id: str, tc_id: str, ctx: Context) -> dict:
    """Take this replay's logcat slice, record it, and return the case's crash."""
    try:
        ref = _Ref(run_id, tc_id, ctx)
        prior = _prior_content(run_id, tc_id)
        evidence = _evidence_record(prior.get("evidence"))
        if evidence.get("skipped"):
            return _merge_crash(evidence.get("crash"), None)
        await _take_slice(ref, evidence)
        await _finish_wire(evidence, ref)
        _splice_evidence(ref, evidence, prior)
        return _merge_crash(evidence.get("crash"), None)
    except Exception:  # never-raise: evidence is not a verdict
        logger.exception("mobile.case_runner._slice_evidence failed")
        return {}
