"""Host mode orchestration: the flat prepare-payload envelope and the composed host instructions."""

from __future__ import annotations

from agents.host_orchestration_text import (
    _HOST_GENERATION_INSTRUCTIONS,
    _dedup_instruction,
)
from agents.test_scenario_agent import _category_shared_system
from tools.untrusted import _GUARD

# Payload envelope version -- distinct from the prep-store _SCHEMA_VERSION. Lets
# ops-3d's renderer (and a weaker host) detect an unexpected shape.
_PAYLOAD_VERSION = 1


# Text description of any ticket/attached images (produced server-side by
# _describe_ticket_images). ITEM 6 -- returning the raw images as MCP image
# content -- is DEFERRED to ops-3d, where the MCP tool result is constructed:
# fastmcp 2.14.7 does support image content in a tool result, but that is a
# tool-result concern, not a payload-builder one. This text rides along as the
# parity fallback regardless.
def _image_context(prepared) -> str:
    """The joined image text of the prepared request."""
    return "\n\n".join(
        s
        for s in (
            prepared.jira_image_text,
            prepared.attached_image_text,
            prepared.image_notice,
        )
        if s
    )


def _envelope_base(prepared, prep_id: str, categories: list) -> dict:
    """The flat payload dict, before instructions, orchestration or job stubs."""
    system_prompt = _category_shared_system(prepared.rtm_hint)
    image_context = _image_context(prepared)
    return {
        "version": _PAYLOAD_VERSION,
        "task": "generate_test_cases_host_mode",
        "prep_id": prep_id,
        "system_prompt": system_prompt,
        "user_context": prepared.user_msg,
        "untrusted_data_notice": _GUARD,
        "categories": categories,
        "response_schema": prepared.category_response_schema,
        "image_context": image_context,
    }


# The composed ``instructions`` string, in reading order.
# D3 (2026-08-21): ONE ascending sequence, assembled in reading order.
#
#   _HOST_GENERATION_INSTRUCTIONS  1, 1b, 2   (intro, data, step-zero)
#   _staged_instruction()          3          (seam-gated packet fetch)
#   _finalize_instruction()        4, 5, 6    (generate, Path A, Path B)
#   _dedup_instruction()           7
#   _grounding_instruction()       8          (seam OFF today)
#
# attach_jobs / attach_ambiguity_job PREPEND the 0., 0a., 0d. job
# clauses, so the full payload still ascends. With the seam OFF the
# sequence skips 3 -- a gap, never a duplicate.
#
# HISTORY, kept because it is the argument against re-wording this
# again: the retired fan-out block was moved from 61% to 47% of the way
# through this string on 2026-08-03 and the next measured run ignored it
# anyway. Prominence was not the binding constraint; the ask was.
def compose_instructions(staged: str, finalize: str, grounding: str) -> str:
    """The composed ``instructions`` string, in reading order."""
    return (
        _HOST_GENERATION_INSTRUCTIONS
        + staged
        + finalize
        + _dedup_instruction()
        # LAST on purpose: it must read after the numbered generation steps and
        # after the duplicate review it tells the host to follow.
        + grounding
    )
