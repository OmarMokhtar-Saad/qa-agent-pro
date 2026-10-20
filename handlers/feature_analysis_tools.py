"""MCP registration adapters: feature_analysis_tools."""

from typing import Any

from mcp.types import TextContent

from handlers import _common, feature_analysis
from handlers.feature_analysis_types import FeatureAnalysisCall


# Optional tool — only in the FULL edition, and only when the Feature
# Analysis feature is on. 2026-08-03: the public qa-agent-pro build is
# deliberately test-cases-only AND credential-free, and this PAIR was the
# last tester-facing path there that could reach a server-side LLM backend
# (its `mobile` / `jira_mobile` modes describe captured screens through
# this server's own ask_vision, tools/image_description.py). The edition
# EDITION gate is what protects the dist, and it still does: the flag was
# DELETED on 2026-08-14 (batch 8c) and hardcoded ON, so test_cases_only()
# is now the ONLY thing standing between the public build and this pair.
# (Since 2026-08-15 the mobile modes reach NO ask_vision: the captured
# screens are attached to the reply as MCP image content for the tester's
# own model.) It is checked here and again inside both handlers,
# deliberately.
def register_feature_analysis_tools(mcp, kit, rt) -> None:
    """Register the Feature Analysis pair (full edition, feature on)."""
    if not _common.test_cases_only():
        _register_fa_start_tool(mcp, kit, rt)
        _register_fa_submit_tool(mcp, kit, rt)


def _fa_call(rt, ctx, args: tuple) -> FeatureAnalysisCall:
    """Build the call from the raw tool arguments (text, mode, device, serial, jira)."""
    feature_or_url, mode, device_id, serial, jira_content_json = args
    return FeatureAnalysisCall(
        feature_or_url,
        mode=mode,
        device_id=rt._device_ref(device_id, serial),
        choose=rt._make_chooser(ctx),
        progress=rt._make_progress(ctx),
        jira_content_json=jira_content_json,
    )


def _fa_blocks(rt, reply) -> list:

    # Captured screens ride to the tester's own multimodal model as
    # image content. getattr: every other return path is a plain str.
    return [
        TextContent(type="text", text=str(reply)),
        *rt._image_content_blocks(getattr(reply, "images", ())),
    ]


def _register_fa_start_tool(mcp, kit, rt) -> None:
    Context: Any = kit.context
    ContentBlock: Any = kit.content_block

    @mcp.tool()
    async def qa_feature_analysis(  # noqa: PLR0913 - MCP tool schema
        ctx: Context,
        feature_or_url: str = "",
        mode: str = "",
        device_id: str = "",
        jira_content_json: str = "",
        serial: str = "",
    ) -> list[ContentBlock]:
        """Start a compact Feature Analysis Report. Chat-only: returns a task envelope YOU answer, then call qa_submit_feature_analysis with the task_id and your JSON report. mode: jira | mobile | jira_mobile (omit: the server asks). A Jira URL may first return a DIRECTIVE: follow it."""
        args = (feature_or_url, mode, device_id, serial, jira_content_json)
        coro = feature_analysis.handle_feature_analysis(_fa_call(rt, ctx, args))
        reply = await rt._tracked_noted("qa_feature_analysis", ctx, coro)
        return _fa_blocks(rt, reply)


def _register_fa_submit_tool(mcp, kit, rt) -> None:
    Context: Any = kit.context

    @mcp.tool()
    async def qa_submit_feature_analysis(
        task_id: str, report_json: str, ctx: Context
    ) -> str:
        """Submit the Feature Analysis JSON YOU wrote for a task opened by qa_feature_analysis: ONE JSON object matching the envelope's response_schema, as `report_json` with the task_id. Invalid input gets ONE resubmit round against a new task_id."""
        return await rt._tracked(
            "qa_submit_feature_analysis",
            ctx,
            feature_analysis.handle_submit_feature_analysis(
                task_id, report_json, progress=rt._make_progress(ctx)
            ),
        )
