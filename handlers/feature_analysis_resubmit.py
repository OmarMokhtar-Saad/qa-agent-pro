"""The Feature Analysis resubmit round for an unusable report."""

from __future__ import annotations

import logging

from agents.feature_analysis import prepare_feature_analysis
from agents.feature_analysis_request import FeatureAnalysisRequest
from handlers import explore_bug

logger = logging.getLogger("tools.mcp_handlers")

# First submission plus ONE structured resubmit round. Re-running the PREPARE
# instead is not equivalent on the mobile modes -- it would re-drive the device
# capture-another loop -- so the prompt inputs ride on the task record and a
# round 2 rebuilds the prompt from them against a NEW task id (a task id is
# one-shot). A still-unusable round 2 renders the blank report WITH a warning:
# never discard the tester's turn, never pretend it worked.
_MAX_FEATURE_ANALYSIS_ROUNDS = 2


def _fa_resubmit_request(meta: dict, round_no: int) -> FeatureAnalysisRequest:
    return FeatureAnalysisRequest(
        str(meta.get("feature_text") or ""),
        str(meta.get("jira_text") or ""),
        str(meta.get("screen_descriptions") or ""),
        mode=str(meta.get("mode") or ""),
        screens=int(meta.get("screens") or 0),
        source=str(meta.get("source") or ""),
        round_no=round_no + 1,
    )


def _fa_resubmit_text(reopened_content: dict) -> str:
    return (
        "> ⚠️ That submission carried no usable Feature Analysis "
        "object. Re-emit it as a SINGLE JSON object matching "
        "`response_schema` and submit it against the NEW task id "
        "below.\n\n"
        + explore_bug.shape_host_task(
            explore_bug.HostTaskSpec(
                "Feature Analysis — resubmit (round 2 of 2)",
                str(reopened_content.get("task_id") or ""),
                "qa_submit_feature_analysis",
                "field `report_json`",
                response_schema=(reopened_content.get("envelope") or {}).get(
                    "response_schema"
                ),
            ),
            reopened_content.get("envelope") or {},
        )
    )


async def _fa_resubmit_reply(task_id: str, meta: dict, usable: bool) -> str | None:
    """The round-2 resubmit task for an unusable report, or None to render as is."""
    round_no = int(meta.get("round") or 1)
    if usable or round_no >= _MAX_FEATURE_ANALYSIS_ROUNDS:
        return None
    reopened = await prepare_feature_analysis(_fa_resubmit_request(meta, round_no))
    reopened_content = reopened.get("content") or {}
    if not reopened.get("error") and reopened_content.get("task_id"):
        return _fa_resubmit_text(reopened_content)
    logger.warning(
        "could not open a resubmit round for feature-analysis task %s — "
        "rendering what the host sent",
        task_id,
    )
    return None
