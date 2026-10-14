"""Read, gate and review stage of qa_submit_suite (C3a step 4).

Sequences read -> intake -> gates -> ambiguity -> reviews -> images -> dup in the
order the handler always ran them. A ``str`` return anywhere is a ready refusal
reply; every side effect happens in the stage modules, in the same order.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from agents import (
    host_submit_ambiguity,
    host_submit_dup,
    host_submit_gates,
    host_submit_images,
    host_submit_intake,
    host_submit_read,
)
from agents.host_submit_ambiguity import AmbIn
from agents.host_submit_gates import GatesIn
from agents.host_submit_images import ImageIn
from agents.host_submit_ports_prep import AskCb, ProgressCb, SubmitPorts
from agents.host_submit_reviews_ac import ac_review
from agents.host_submit_reviews_checklist import checklist_review
from agents.host_submit_reviews_grounding import grounding_review
from agents.host_submit_types import (
    ReviewIn,
    SubAcks,
    SubAmb,
    SubCases,
    SubGates,
    SubPrep,
    SubRead,
    SubReq,
    SubRun,
)


class SubmitCall(NamedTuple):
    """One qa_submit_suite call: the sanitised id, the payload, acks, callbacks."""

    prep_id: str
    suite_json: Any
    acks: SubAcks
    cbs: tuple[AskCb, ProgressCb]


class Front(NamedTuple):
    prep: SubPrep
    read: SubRead
    cases: SubCases
    gates: SubGates
    amb: SubAmb


async def _gated(
    call: SubmitCall, prep: SubPrep, ports: SubmitPorts
) -> tuple[SubRead, SubCases, SubGates] | str:
    full = host_submit_read.has_full_submission(call.suite_json)
    req = SubReq(call.prep_id, prep.envelope, prep.meta, call.suite_json, full)
    read = await host_submit_read.read_submission(req, ports)
    if isinstance(read, str):
        return read
    cases = await host_submit_intake.case_intake(call.prep_id, read, ports)
    gates_in = GatesIn(prep, cases, read.parsed, call.acks)
    gates = await host_submit_gates.submit_gates(gates_in, ports)
    return gates if isinstance(gates, str) else (read, cases, gates)


async def _front(call: SubmitCall, prep: SubPrep, ports: SubmitPorts) -> Front | str:
    got = await _gated(call, prep, ports)
    if isinstance(got, str):
        return got
    read, cases, gates = got
    amb_in = AmbIn(call.prep_id, prep.meta, read.parsed, cases.staged_rows)
    amb = await host_submit_ambiguity.review(amb_in, ports)
    return amb if isinstance(amb, str) else Front(prep, read, cases, gates, amb)


async def _back(call: SubmitCall, front: Front, ports: SubmitPorts) -> SubRun | str:
    prep, read, cases = front.prep, front.read, front.cases
    rev_in = ReviewIn(prep.meta, read.parsed, prep.prepared, cases.all_cases)
    ac = ac_review(rev_in, ports)
    ground = grounding_review(read.parsed, cases.all_cases, ports)
    checklist = checklist_review(rev_in, ports)
    img_in = ImageIn(prep, read.parsed, cases.staged_rows, call.acks)
    images = await host_submit_images.review(img_in, ports)
    if isinstance(images, str):
        return images
    dup_in = host_submit_dup.DupIn(read.parsed, read.has_full, ground.all_cases)
    dup = host_submit_dup.review(dup_in, ports)
    rest = (ac, ground, checklist, images, dup)
    return SubRun(prep, read, cases, front.gates, front.amb, *rest)


async def build_run(
    call: SubmitCall, prep: SubPrep, ports: SubmitPorts
) -> SubRun | str:
    """Read, gate and review the submission; a str return is a ready refusal."""
    front = await _front(call, prep, ports)
    if isinstance(front, str):
        return front
    return await _back(call, front, ports)
