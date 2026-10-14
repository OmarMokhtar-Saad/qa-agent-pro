"""Host mode: assembling one category job packet and the hoisted batch packet."""

from __future__ import annotations

from agents.host_category_consts import _EXAMPLE_VALID_CASE
from agents.host_category_text import _category_worker_instructions
from agents.host_orchestration import _prepared_ac_entries
from tools.untrusted import _GUARD


# The criteria the SERVER already knows (``acceptance_criteria``), in the shape the
# parent would otherwise have to fill by hand:
# [{"ac_id": "AC-001", "description": "..."}, ...]. Sourced from
# prepared.acs -- the same list rtm_hint is rendered from -- so this field
# AGREES with the system prompt instead of contradicting it. EMPTY only when
# the server truly has none (the AC_JOB boomerang case), where the PARENT still
# fills it from step 0b before dispatch; the worker_instructions describe both.
#
# v1.97.0 cursor-hardening (item 4a): ``example_case`` is a schema-valid example
# case -- ground truth for enum spelling and list-field shape.
def _job_packet(prepared, prep_id: str, parts: tuple) -> dict:
    """The self-contained packet for one category; ``parts`` is ``_category_prompt_parts``."""
    name, ptype, system_prompt, instruction, min_count, max_count = parts
    return {
        "prep_id": prep_id or "",
        "category_name": name,
        "system_prompt": system_prompt,
        "user_context": prepared.user_msg,
        "untrusted_data_notice": _GUARD,
        "instruction": instruction,
        "response_schema": prepared.category_response_schema,
        "min_cases": min_count,
        "max_cases": max_count,
        "preferred_type": ptype,
        "acceptance_criteria": _prepared_ac_entries(prepared),
        "example_case": _EXAMPLE_VALID_CASE,
        "worker_instructions": _category_worker_instructions(prepared),
    }


def _shared_block(job: dict) -> dict:
    """The fields of ``job`` that are stated once in the batch packet."""
    return {
        "prep_id": job["prep_id"],
        "system_prompt": job["system_prompt"],
        "user_context": job["user_context"],
        "untrusted_data_notice": job["untrusted_data_notice"],
        "response_schema": job["response_schema"],
        "min_cases": job["min_cases"],
        "max_cases": job["max_cases"],
        "acceptance_criteria": job["acceptance_criteria"],
        "example_case": job["example_case"],
        "worker_instructions": job["worker_instructions"],
    }


def _job_entry(job: dict) -> dict:
    """The per-category part of ``job`` in the batch packet."""
    return {
        "category_name": job["category_name"],
        "instruction": job["instruction"],
        "preferred_type": job["preferred_type"],
    }


def _collect_batch(prepared, prep_id: str, build) -> dict | None:
    """The batch packet from ``build(prepared, prep_id, name)`` for every category, or None."""
    names = [c[0] for c in getattr(prepared, "categories", None) or []]
    shared = None
    jobs = []
    for name in names:
        job = build(prepared, prep_id, name)
        if job is None:
            continue
        if shared is None:
            shared = _shared_block(job)
        jobs.append(_job_entry(job))
    if shared is None or not jobs:
        return None
    return {"shared": shared, "jobs": jobs}
