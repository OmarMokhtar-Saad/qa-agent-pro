"""MCP registration adapters: feature_analysis_tools."""

from handlers import _common, feature_analysis


def register_feature_analysis_tools(mcp, Context, ContentBlock, rt) -> None:
    """Register the Feature Analysis pair (full edition, feature on)."""
    # Optional tool — only in the FULL edition, and only when the Feature
    # Analysis feature is on. 2026-08-03: the public qa-agent-pro build is
    # deliberately test-cases-only AND credential-free, and this PAIR was the
    # last tester-facing path there that could reach a server-side LLM backend
    # (its `mobile` / `jira_mobile` modes describe captured screens through
    # this server's own ask_vision, tools/image_description.py). The edition
    # EDITION gate is what protects the dist, and it still does: the flag was
    # DELETED on 2026-08-14 (batch 8c) and hardcoded ON, so _test_cases_only()
    # is now the ONLY thing standing between the public build and this pair.
    # (Since 2026-08-15 the mobile modes reach NO ask_vision: the captured
    # screens are attached to the reply as MCP image content for the tester's
    # own model.) It is checked here and again inside both handlers,
    # deliberately.
    if rt._feature_analysis_enabled() and not _common._test_cases_only():

        @mcp.tool()
        async def qa_feature_analysis(
            ctx: Context,
            feature_or_url: str = "",
            mode: str = "",
            device_id: str = "",
            jira_content_json: str = "",
            serial: str = "",
        ) -> list[ContentBlock]:
            """Start a compact enterprise Feature Analysis Report. Chat-only: no model call; it returns a task envelope YOU answer, then call qa_submit_feature_analysis with the task_id and your JSON report. mode: jira (description or Jira URL), mobile (capture device screens) or jira_mobile; omit it and the server asks. A Jira URL may first return a DIRECTIVE: fetch with your own mcp__atlassian__getJiraIssue and call again with jira_content_json set to the raw result as a JSON STRING."""
            from mcp.types import TextContent

            reply = await rt._tracked_noted(
                "qa_feature_analysis",
                ctx,
                feature_analysis.handle_feature_analysis(
                    feature_or_url,
                    mode=mode,
                    device_id=rt._device_ref(device_id, serial),
                    choose=rt._make_chooser(ctx),
                    progress=rt._make_progress(ctx),
                    jira_content_json=jira_content_json,
                ),
            )
            # Captured screens ride to the tester's own multimodal model as
            # image content. getattr: every other return path is a plain str.
            return [
                TextContent(type="text", text=str(reply)),
                *rt._image_content_blocks(getattr(reply, "images", ())),
            ]

        @mcp.tool()
        async def qa_submit_feature_analysis(
            task_id: str, report_json: str, ctx: Context
        ) -> str:
            """Submit the Feature Analysis JSON YOU wrote for a task opened by qa_feature_analysis. Produce a SINGLE JSON object matching the envelope's response_schema and pass it with the task_id as `report_json`. The server validates it, renders the report, and, if the submission carried no usable object, gives ONE resubmit round against a new task_id."""
            return await rt._tracked(
                "qa_submit_feature_analysis",
                ctx,
                feature_analysis.handle_submit_feature_analysis(
                    task_id, report_json, progress=rt._make_progress(ctx)
                ),
            )
