"""MCP registration adapters: core_tools."""

from typing import Any

from handlers import core_tools_docs as _docs
from handlers import core_tools_run as _run


def register_core_tools(mcp, kit, rt) -> None:
    """Register the test-case workflow tools every edition ships."""
    _register_generate_tool(mcp, kit, rt)
    _register_stage_jira_tool(mcp, kit, rt)
    _register_prepare_tool(mcp, kit, rt)


def _register_generate_tool(mcp, kit, rt) -> None:
    """Register qa_generate_test_cases."""
    Context: Any = kit.context

    @_run.tool_with_doc(mcp, _docs.GENERATE_DOC)
    async def qa_generate_test_cases(  # noqa: PLR0913 - MCP tool schema
        ctx: Context,
        feature_or_url: str = "",
        proceed_anyway: bool = False,
        jira_content_json: str = "",
        stage_token: str = "",
        source_plan: str = "",
        attached_image_count: int = 0,
        capture_ids: list[str] | None = None,
        image_gate_ack: bool = False,
        image_carry_ack: bool = False,
    ) -> str:
        return await _run.generate_call(rt, locals())


def _register_stage_jira_tool(mcp, kit, rt) -> None:
    """Register qa_stage_jira."""
    Context: Any = kit.context

    @_run.tool_with_doc(mcp, _docs.STAGE_JIRA_DOC)
    async def qa_stage_jira(  # noqa: PLR0913 - MCP tool schema
        ctx: Context, stage_token: str, part: str, json: str
    ) -> str:
        return await _run.stage_call(rt, locals())


def _register_prepare_tool(mcp, kit, rt) -> None:
    """Register qa_prepare_test_cases."""
    Context: Any = kit.context
    ContentBlock: Any = kit.content_block

    @_run.tool_with_doc(mcp, _docs.PREPARE_DOC)
    async def qa_prepare_test_cases(  # noqa: PLR0913 - MCP tool schema
        ctx: Context,
        feature_or_url: str = "",
        proceed_anyway: bool = False,
        jira_content_json: str = "",
        stage_token: str = "",
        source_plan: str = "",
        attached_image_count: int = 0,
        capture_ids: list[str] | None = None,
        image_gate_ack: bool = False,
        image_carry_ack: bool = False,
    ) -> list[ContentBlock]:
        return await _run.prepare_call(rt, locals())
