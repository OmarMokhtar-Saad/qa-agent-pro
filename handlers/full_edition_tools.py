"""MCP registration adapters: full_edition_tools."""

from typing import Any

from handlers import _common, explore_bug


def register_full_edition_tools(mcp, kit, rt) -> None:
    """Register the bug-report and exploratory tools (full edition only)."""
    # Full edition only — the distribution build exposes test-case tools alone.
    if not _common.test_cases_only():
        _register_bug_report_tool(mcp, kit, rt)
        _register_explore_step_tool(mcp, kit, rt)


def _register_bug_report_tool(mcp, kit, rt) -> None:
    """Register qa_bug_report."""
    Context: Any = kit.context

    @mcp.tool()
    async def qa_bug_report(description: str, ctx: Context) -> str:
        """Start a structured bug report from a plain-language description. Chat-only: the server makes NO model call. It returns a task envelope that YOU answer, then call qa_submit_bug_report with the task_id and your markdown report."""
        return await rt._tracked(
            "qa_bug_report",
            ctx,
            explore_bug.handle_bug_report(description, progress=rt._make_progress(ctx)),
        )


def _register_explore_step_tool(mcp, kit, rt) -> None:
    """Register qa_explore_step."""
    Context: Any = kit.context

    @mcp.tool()
    async def qa_explore_step(  # noqa: PLR0913 - MCP tool schema
        feature: str, session_id: str, ctx: Context, tester_response: str = ""
    ) -> str:
        """Start the next step of an exploratory-testing coaching session. Chat-only: the server returns a task envelope that YOU answer, then call qa_submit_explore_step with the task_id and your coaching step. Pass a stable session_id to keep coverage memory across calls; include tester_response with what you observed after the previous step."""
        opts = explore_bug.ExploreStepOpts(tester_response, rt._make_progress(ctx))
        return await rt._tracked(
            "qa_explore_step",
            ctx,
            explore_bug.handle_explore_step(feature, session_id, opts),
        )
