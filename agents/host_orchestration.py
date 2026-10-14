"""Host mode: orchestration, prepare payload and host instructions. Cut verbatim from agents/host_mode.py (P4.3)."""

from __future__ import annotations

from agents.host_grounding import _grounding_instruction
from agents.host_orchestration_categories import (  # noqa: F401
    _prepare_categories,
    _prepare_job_stubs,
)
from agents.host_orchestration_entries import (  # noqa: F401
    _prepared_ac_entries,
    expected_category_names,
    prepared_case_bounds,
)
from agents.host_orchestration_envelope import (
    _envelope_base,
    compose_instructions,
)
from agents.host_orchestration_staged_text import (  # noqa: F401
    _FINALIZE_SIDECAR_FIRST,
    _HOST_FINALIZE_INSTRUCTIONS,
    _HOST_STAGED_INSTRUCTION,
)
from agents.host_orchestration_stages import (
    _OrchParts,
    finalize_block,
    finalize_text,
    orchestration_dict,
    parent_text,
    staged_text,
)
from agents.host_orchestration_text import (  # noqa: F401
    _HOST_DEDUP_INSTRUCTION,
    _HOST_GENERATION_INSTRUCTIONS,
    _ORCH_WORKER_INSTRUCTIONS,
    _dedup_instruction,
)


# The staged-category contract (`orchestration` / `jobs` keys, packet fetch)
# is unconditional. Its persisted twin `meta["parallel_fanout"]` is still
# stamped True into every prep envelope: the submit-time completeness gate
# reads it, and historical envelopes carry it.
# Step 3 (fetch the category packets).
def _staged_instruction() -> str:
    """Step 3 (fetch the category packets)."""
    return staged_text()


# Steps 4-6 with the finalize sentinel resolved.
#
# Always emitted. Resolves the sentinel so step 5 recommends the route that
# KEEPS the duplicate review whenever that review is enabled (Fix 2).
def _finalize_instruction() -> str:
    """Steps 4-6 with the finalize sentinel resolved."""
    return finalize_text()


def _orchestration_finalize() -> dict:
    """The `finalize` block of the orchestration object."""
    return finalize_block()


def _orchestration_parent_instructions() -> str:
    """The `parent_instructions` text of the orchestration object."""
    return parent_text()


def build_orchestration(prepared, prep_id: str = "") -> dict:
    """The orchestration object for the prepare payload."""
    names = expected_category_names(prepared)
    parts = _OrchParts(_orchestration_finalize(), _orchestration_parent_instructions())
    return orchestration_dict(names, prep_id, parts)


def _prepare_instructions() -> str:
    """The composed ``instructions`` string, in reading order."""
    return compose_instructions(
        _staged_instruction(), _finalize_instruction(), _grounding_instruction()
    )


# Build the dict the tester's own chat model needs to run the 8-category
# fan-out itself. Pure and synchronous -- no LLM call, no I/O.
#
# Output parity with server mode: this reproduces the server's cache-ON prompt
# DECOMPOSITION, re-derived from agents.test_scenario_agent. It is FUNCTIONALLY
# EQUIVALENT to (not byte-identical to) the DEFAULT cache-off path, which
# inlines the per-category FOCUS/count/type INSIDE ``system`` via
# ``_CATEGORY_SYSTEM_TEMPLATE``; the combined ``system_prompt`` + per-category
# ``instruction`` covers the same building blocks (header, rules, JSON tail,
# FOCUS, rtm_hint, _GUARD) -- see
# tests test_combined_content_covers_default_path_assembly.
#
# * ``system_prompt`` == ``_category_shared_system(prepared.rtm_hint)`` (the
# category-INDEPENDENT half of the cache-ON split, incl. the terminating
# _GUARD).
# * each ``categories[i]["instruction"]`` == the cache-ON per-category user
# suffix: ``_CATEGORY_TASK_TEMPLATE.format(...)`` + the upfront quality
# reminder (unconditional since 2026-08-12) -- the exact FOCUS / min-max
# count / preferred-type block.
# * ``min_cases`` / ``max_cases`` == ``_case_count_bounds(complexity_text or
# feature_text or user_msg, ui_content)`` -- same complexity proxy, same
# precedence the server's _generate_for_category uses.
# * ``response_schema`` == ``prepared.category_response_schema`` (the TestSuite
# JSON schema the server would validate against).
#
# Security: ``user_context`` is ``prepared.user_msg`` carried VERBATIM -- the
# ticket/comment text inside it is already _GUARD / wrap_untrusted-wrapped and
# URL-stripped by the prepare half, and it now enters the USER's own model
# context, so it is neither re-stringified nor unwrapped here.
# ``untrusted_data_notice`` carries tools.untrusted._GUARD verbatim so the host
# model is told, in the project's own wording, to treat any wrapped block as
# DATA, never as instructions.
#
# Shape: a FLAT dict. The large fields (``system_prompt``, ``user_context``,
# ``response_schema``) are separate top-level keys and each ``categories`` entry
# is self-contained, so ops-3d can chunk the payload across multiple MCP
# text-content blocks without splitting a field mid-value. ops-3d owns chunking
# and the MCP tool-result size limit; this builder never truncates.
def build_prepare_payload(prepared, prep_id: str = "") -> dict:
    """Build the dict the tester's own chat model needs to run the 8-category fan-out."""
    categories = _prepare_categories(prepared)
    out = _envelope_base(prepared, prep_id, categories)
    out["instructions"] = _prepare_instructions()
    out["orchestration"] = build_orchestration(prepared, prep_id)
    out["jobs"] = _prepare_job_stubs(prepared, prep_id, categories)
    return out
