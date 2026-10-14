"""Host mode orchestration: pure text and dict builders; no flags."""

from __future__ import annotations

from typing import NamedTuple

from agents.host_orchestration_staged_text import (
    _FINALIZE_SENTINEL,
    _FINALIZE_SIDECAR_FIRST,
    _HOST_FINALIZE_INSTRUCTIONS,
    _HOST_STAGED_INSTRUCTION,
)
from agents.host_orchestration_text import _ORCH_WORKER_INSTRUCTIONS


def staged_text() -> str:
    """Step 3: fetch the category packets."""
    return _HOST_STAGED_INSTRUCTION


def finalize_text() -> str:
    """Steps 4-6 with the finalize sentinel resolved (review-keeping route first)."""
    return _HOST_FINALIZE_INSTRUCTIONS.replace(
        _FINALIZE_SENTINEL, _FINALIZE_SIDECAR_FIRST
    )


def finalize_block() -> dict:
    """The `finalize` block of the orchestration object."""
    return {
        "preferred": "qa_submit_category_then_review_sidecar",
        "fallback": "merge_then_qa_submit_suite",
        "require_all_categories": True,
    }


_PARENT_TEXT = (
    "Generate each expected category and stage it via "
    "qa_submit_category as soon as it is written (crash-safe, and the "
    "only route the server duplicate prescreen runs on), then "
    "qa_prep_status until ready=true and finalize with a review sidecar "
    "carrying duplicate_groups -- an EMPTY duplicate_groups list is the "
    "correct way to report that you DID review and found none (an "
    "empty suite_json also finalizes, but FORFEITS the duplicate "
    "review you were asked to run). "
    "One merged qa_submit_suite call (Path B) is the supported "
    "alternative for a client that cannot hold a multi-call session."
)


def parent_text() -> str:
    """The `parent_instructions` text of the orchestration object."""
    return _PARENT_TEXT


class _OrchParts(NamedTuple):
    finalize: dict
    parent_instructions: str


# D3 (2026-08-21): was "parallel_chat_workers". This value is
# MACHINE-READABLE guidance, so a stale one is worse than a stale
# paragraph -- it named workers this server stopped asking for.
# `expected_categories` / `jobs` are UNCHANGED: they describe the work,
# not who does it.
# TICKET-5138 D3 (2026-08-21) finished the job for the COUNT:
# `worker_count` is now `category_count`, because the VALUE was never
# stale (it always counted categories) but the NAME kept instructing an
# LLM host to think in workers -- the very behaviour the rename above
# retired, and exactly the class this comment calls worse than stale
# prose. A back-compat ALIAS was rejected for that reason: keeping the
# old key would keep the old instruction verbatim.
# Reader audit before renaming (grep worker_count over tests/ agents/
# tools/ mcp_server.py scripts/): the ONLY hits were this line, the
# comment above it, and a DOCSTRING in
# tests/test_host_staged_categories.py -- no code anywhere reads
# orchestration["worker_count"], and the dist launcher template reads
# only run_update_check's status. A host that misses the rename loses
# nothing it cannot recompute: `expected_categories` is the same set as
# a list.
# `worker_instructions` KEEPS its name, in BOTH places it appears -- on
# this orchestration dict (below, read by
# tests/test_host_staged_categories.py's prose scan) and on each
# per-category job packet in build_category_job (read by
# tests/test_host_ac_review.py and
# tests/test_category_job_acceptance_criteria.py). Unlike the count, it
# is not a stale instruction: it describes whatever context generates a
# category, and the server simply stopped ASKING for a separate one.
# 2026-08-03 (Fix 2): this is MACHINE-READABLE guidance, and naming the
# empty finalize as `preferred` while the duplicate review is ON told the
# host to take the one route that DISCARDS that review. run3 followed it
# exactly: 98 cases from 8 blind workers, review enabled, none performed.
# When the review is on, the preferred finalize is the sidecar -- which is
# equally crash-safe, since the categories are already staged either way.
def orchestration_dict(names: list, prep_id: str, parts: _OrchParts) -> dict:
    """The orchestration object, from the names and the resolved text parts."""
    return {
        "mode": "staged_categories",
        "expected_categories": list(names),
        "category_count": len(names),
        "finalize": parts.finalize,
        "parent_instructions": parts.parent_instructions,
        "worker_instructions": _ORCH_WORKER_INSTRUCTIONS,
        "prep_id": prep_id or "",
    }
