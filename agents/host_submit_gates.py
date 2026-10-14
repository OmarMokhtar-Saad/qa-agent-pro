"""Volume-floor and step-assertion gates of qa_submit_suite (C3a step 2a).

Batch 1 (2026-08-09) volume gate and F4 (2026-08-29) step-assertion gate, in
that order, on BOTH finalize routes. They run after intake normalised every
self-reported ``category`` and before the ambiguity gate, the finalize, the
export and the persist, so a refusal costs one round trip and destroys
nothing. ``version_note`` is prefixed to each refusal: a prep staged on one
install and submitted to another must not lose it to an early return.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from agents.host_category_calls import AssertionGateIn, VolumeGateIn
from agents.host_submit_ports_prep import SubmitPorts
from agents.host_submit_types import SubAcks, SubCases, SubGates, SubPrep
from tools import step_assertion


class GatesIn(NamedTuple):
    prep: SubPrep
    cases: SubCases
    parsed: Any
    acks: SubAcks


async def _volume(args: GatesIn, ports: SubmitPorts) -> tuple[Any, str]:
    p, c = args.prep, args.cases
    return await ports.category.volume_floor_gate(
        VolumeGateIn(
            p.prep_id, p.envelope, c.all_cases, c.staged_rows, args.acks.volume_floor
        )
    )


async def _assertion(args: GatesIn, ports: SubmitPorts) -> tuple[Any, str, Any]:
    p, c = args.prep, args.cases
    findings = step_assertion.find_tautological_steps(c.all_cases)
    refusal, note = await ports.category.step_assertion_gate(
        AssertionGateIn(
            p.prep_id, p.envelope, c.all_cases, findings, args.acks.step_assertion
        )
    )
    return refusal, note, findings


async def submit_gates(args: GatesIn, ports: SubmitPorts) -> str | SubGates:
    version_note = args.prep.version_note
    refusal, volume_note = await _volume(args, ports)
    if refusal is not None:
        return f"{version_note}{refusal}"
    refusal, assertion_note, findings = await _assertion(args, ports)
    if refusal is not None:
        return f"{version_note}{refusal}"
    data_findings = step_assertion.find_echoed_test_data(args.cases.all_cases)
    dropped_note = ports.prep_gate.dropped_note(args.parsed)
    return SubGates(volume_note, assertion_note, findings, data_findings, dropped_note)
