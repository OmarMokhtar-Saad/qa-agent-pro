"""Prompt assembly for Feature Analysis (leaf of feature_analysis)."""

from __future__ import annotations

from agents.feature_analysis_blocks import _ac_block, _shots_block, _ui_block
from agents.feature_analysis_prompts import (
    _RESUBMIT_REMINDER,
    _SYSTEM_PROMPT,
    _SYSTEM_PROMPT_NO_SCREENS,
)
from agents.feature_analysis_request import FeatureAnalysisRequest
from tools.untrusted import _GUARD, wrap_untrusted


# PROVENANCE NOTE for the host path: the blocks below are each wrapped
# individually, but ``host_llm.build_envelope`` re-wraps the whole context and
# COLLAPSES them into ONE block, stripping the per-source labels (documented
# consequence #1 of the unconditional wrap). The plain markdown headings
# (``## Feature / Ticket Text``, ``## Parsed Acceptance Criteria``, ...) live in
# the BODY precisely so the host can still tell the sources apart.
def _user_message(request: FeatureAnalysisRequest) -> str:
    # Bound token cost: cap the primary text before wrapping (reviewer note 1).
    primary = (
        request.jira_text or request.feature_text or ""
    ).strip() or "(none provided)"
    return (
        "## Feature / Ticket Text\n"
        + wrap_untrusted("jira_or_web_content", primary)
        + _ac_block(list(request.acs or []))
        + _shots_block(request.screenshot_descriptions, request.screens)
        + _ui_block(request.ui_content)
    )


# Extracted VERBATIM from the former ``analyze_feature`` body so the chat-only
# host path and the legacy coroutine share ONE prompt and cannot drift.
# ``reminder`` is the resubmit instruction inserted between the rubric and
# ``_GUARD`` on a retry round.
def build_feature_analysis_prompt(
    request: FeatureAnalysisRequest, reminder: str = ""
) -> tuple[str, str]:
    """Return ``(system_prompt, user_message)`` for ONE Feature Analysis pass; pure, never raises."""
    has_screens = bool(request.screenshot_descriptions) or int(request.screens or 0) > 0
    rubric = _SYSTEM_PROMPT if has_screens else _SYSTEM_PROMPT_NO_SCREENS
    return rubric + (reminder or "") + _GUARD, _user_message(request)


def _round_prompt(request: FeatureAnalysisRequest) -> tuple[str, str]:
    """The (system, user) prompt for this round; a resubmit adds the reminder."""
    reminder = _RESUBMIT_REMINDER if int(request.round_no) > 1 else ""
    return build_feature_analysis_prompt(request, reminder)
