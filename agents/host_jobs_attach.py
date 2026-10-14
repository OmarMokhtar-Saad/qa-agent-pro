"""Host mode: attach HostJobs to a prepare payload and build the jobs_to_run index (split from host_jobs.py)."""

from __future__ import annotations

from typing import NamedTuple

from agents.host_jobs_core import (
    _AMBIGUITY_JOB_INSTRUCTIONS,
    _JOB_STAGE_RANK,
    _LEGACY_JOB_KEYS,
    HostJob,
)


def _job_index_entry(payload_key, fields) -> dict:
    """Index entry for ``payload_key``; ``fields`` is
    ``(job_id, stage, order, blocking, return_field)``."""
    job_id, stage, order, blocking, return_field = fields
    return {
        "job_id": str(job_id),
        "stage": str(stage),
        "order": int(order),
        "blocking": bool(blocking),
        "return_field": str(return_field or ""),
        "payload_key": str(payload_key),
    }


def _legacy_job_index(out: dict, instr0: str) -> tuple[list, str]:
    """Index entries for legacy job keys present in `out`, plus the instruction
    clauses still missing from `instr0`."""
    index: list = []
    clauses = ""
    for key, meta in _LEGACY_JOB_KEYS.items():
        if isinstance(out.get(key), dict):
            jid, stage, order, blocking, ret, marker, clause = meta
            index.append(_job_index_entry(key, (jid, stage, order, blocking, ret)))
            if clause and marker and marker not in instr0:
                clauses += clause
    return index, clauses


def _prefixed_instructions(instr: str, legacy_clauses: str, jobs) -> str:
    """`instr` with the legacy clauses and each job's step text prefixed."""
    prefix = legacy_clauses
    for j in sorted(jobs, key=lambda j: (_JOB_STAGE_RANK.get(j.stage, 9), j.order)):
        if j.marker and j.marker in instr:
            continue
        prefix += j.step_instructions
    if not prefix:
        return instr
    # Keep the ambiguity block FIRST: it is the blocking safety job, and
    # a host that reads only the opening paragraph must read that one.
    if instr.startswith(_AMBIGUITY_JOB_INSTRUCTIONS):
        head = _AMBIGUITY_JOB_INSTRUCTIONS
        return head + prefix + instr[len(head) :]
    return prefix + instr


class _Legacy(NamedTuple):
    """The legacy instruction clauses still to prefix, and the HostJobs that were attached."""

    clauses: str
    jobs: list


def _index_jobs(out: dict, jobs) -> tuple[list, _Legacy]:
    """Stamp each HostJob's spec onto ``out``; return the unsorted index and the instruction inputs."""
    jobs = [j for j in (jobs or ()) if isinstance(j, HostJob)]
    index, clauses = _legacy_job_index(out, str(out.get("instructions") or ""))
    for j in jobs:
        out[j.payload_key] = dict(j.spec)
        index.append(
            _job_index_entry(
                j.payload_key,
                (j.job_id, j.stage, j.order, j.blocking, j.return_field),
            )
        )
    return index, _Legacy(clauses, jobs)


def _sort_index(index: list) -> list:
    """``index`` sorted in place by (stage rank, order, job_id) and returned."""
    index.sort(
        key=lambda e: (
            _JOB_STAGE_RANK.get(e["stage"], 9),
            e["order"],
            e["job_id"],
        )
    )
    return index


def _stamp_jobs(out: dict, index: list, legacy: _Legacy) -> None:
    """Write ``jobs_to_run`` and the merged step instructions onto ``out``."""
    out["jobs_to_run"] = _sort_index(index)
    instr = str(out.get("instructions") or "")
    merged = _prefixed_instructions(instr, legacy.clauses, legacy.jobs)
    if merged != instr:
        out["instructions"] = merged
