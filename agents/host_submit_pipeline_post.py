"""Finalize and quality-gate stage of qa_submit_suite (C3a step 4).

finalize -> post-dedup volume note -> quality gate -> dup section note, the order
the handler always ran them. A ``str`` return is a ready refusal reply.
"""

from __future__ import annotations

from agents import (
    host_submit_dup_notes,
    host_submit_finalize,
    host_submit_quality,
    host_submit_suite_gate,
)
from agents.host_submit_finalize import VolumeIn
from agents.host_submit_pipeline_run import SubmitCall
from agents.host_submit_ports_prep import SubmitPorts
from agents.host_submit_types import SubFinal, SubPost, SubRun


def _volume_in(call: SubmitCall, run: SubRun, fin: SubFinal) -> VolumeIn:
    return VolumeIn(run.prep.meta, fin.suite, call.prep_id, run.gates.volume_note)


def _dup_note(run: SubRun, fin: SubFinal, ports: SubmitPorts) -> str:
    sect_in = host_submit_dup_notes.SectionIn(
        run.dup, run.cases.submitted_cases, fin.suite
    )
    return host_submit_dup_notes.section_note(sect_in, ports)


async def _suite_gates(run: SubRun, fin: SubFinal, ports: SubmitPorts) -> str | None:
    """K-09 / K-13: refuse once on a contradicted duplicate review or risky warnings."""
    gate = host_submit_suite_gate
    ctx = gate.GateCtx(run.prep, fin.suite, run.read.parsed, run.dup)
    return await gate.dup_gate(ctx, ports) or await gate.quality_gate(
        ctx, ports, fin.summary
    )


async def finish(call: SubmitCall, run: SubRun, ports: SubmitPorts) -> SubPost | str:
    """Finalize, then volume-note and quality-gate; a str is a ready refusal."""
    progress = call.cbs[1]
    fin = await host_submit_finalize.finalize_submit(run, progress, ports)
    if isinstance(fin, str):
        return fin
    vol_in = _volume_in(call, run, fin)
    volume_note = host_submit_finalize.post_dedup_volume_note(vol_in, ports)
    gate_in = host_submit_quality.GateIn(run.prep, fin.suite, call.acks)
    quality = await host_submit_quality.gate(gate_in, ports)
    if isinstance(quality, str):
        return quality
    refusal = await _suite_gates(run, fin, ports)
    if refusal:
        return refusal
    return SubPost(fin, volume_note, quality.note, _dup_note(run, fin, ports))
