"""Host mode: the prompt pieces (system prompt, instruction, bounds) of one category job."""

from __future__ import annotations

import agents.test_scenario_agent as tsa
from agents.host_category_names import _find_category_row


def _count_bounds(prepared) -> tuple:
    """``(min_count, max_count)`` for the generation source of ``prepared``."""
    return tsa._case_count_bounds(
        prepared.complexity_text or prepared.feature_text or prepared.user_msg,
        prepared.ui_content,
    )


def _task_instruction(match: tuple, bounds: tuple) -> str:
    """The task template for ``match`` = ``(name, focus, ptype)`` and ``bounds`` = ``(min, max)``."""
    name, focus, ptype = match
    return (
        tsa._CATEGORY_TASK_TEMPLATE.format(
            category_name=name,
            category_focus=focus,
            preferred_type=ptype,
            min_count=bounds[0],
            max_count=bounds[1],
        )
        + tsa._QUALITY_RULES_UPFRONT
    )


# (name, ptype, system_prompt, instruction, min, max) for canon, or None.
#
# Assembled without recursing through build_prepare_payload's jobs branch.
def _category_prompt_parts(prepared, canon: str) -> tuple | None:
    """(name, ptype, system_prompt, instruction, min, max) for canon, or None."""
    system_prompt = tsa._category_shared_system(prepared.rtm_hint)
    bounds = _count_bounds(prepared)
    match = _find_category_row(prepared, canon)
    if match is None:
        return None
    instruction = _task_instruction(match, bounds)
    return match[0], match[2], system_prompt, instruction, bounds[0], bounds[1]
