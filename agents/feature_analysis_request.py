"""The Feature Analysis task request and its recorded meta (leaf of feature_analysis)."""

from __future__ import annotations

from dataclasses import dataclass

# Cap on each prompt input carried in the task record's meta, so a resubmit round
# can rebuild the prompt WITHOUT re-driving the mobile capture-another loop, and
# without the record growing unbounded (host_llm._cap re-applies its own cap).
_MAX_META_TEXT_CHARS = 20_000


@dataclass(frozen=True)
class FeatureAnalysisRequest:
    """Everything one Feature Analysis round needs."""

    feature_text: str
    jira_text: str = ""
    screenshot_descriptions: str = ""
    ui_content: dict | None = None
    acs: list | None = None
    mode: str = ""
    screens: int = 0
    source: str = ""
    round_no: int = 1
    submit_tool: str = "qa_submit_feature_analysis"


def _task_meta(request: FeatureAnalysisRequest) -> dict:
    """The prompt INPUTS recorded on the host task."""
    return {
        "feature_text": (request.feature_text or "")[:_MAX_META_TEXT_CHARS],
        "jira_text": (request.jira_text or "")[:_MAX_META_TEXT_CHARS],
        "screen_descriptions": (request.screenshot_descriptions or "")[
            :_MAX_META_TEXT_CHARS
        ],
        "mode": str(request.mode or ""),
        "screens": int(request.screens or 0),
        "source": str(request.source or ""),
        "round": int(request.round_no),
    }
