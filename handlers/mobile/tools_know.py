"""MCP registration adapters: the mobile notes, knowledge and flows tools."""

from handlers.mobile import content as mobile_content
from handlers.mobile.content import KnowledgeRequest


def _data_run_id(data: dict | None) -> str:
    """The ``run_id`` a knowledge call's ``data`` names, or ``""``."""
    return str((data or {}).get("run_id") or "")


def _register_mobile_notes_tool(mcp, Context, rt) -> None:
    """Register qa_mobile_notes (mobile lane)."""

    @mcp.tool()
    async def qa_mobile_notes(  # noqa: PLR0913 - MCP tool schema
        ctx: Context,
        action: str = "list",
        package: str = "",
        run_id: str = "",
        note_id: int = 0,
        reason: str = "",
    ) -> str:
        """List or retire (`note_id`, kept in history) ONE app's saved notes, by `package` or `run_id`."""
        return await rt._tracked(
            "qa_mobile_notes",
            ctx,
            mobile_content.handle_mobile_notes(
                mobile_content.NotesRequest(action, package, run_id, note_id, reason)
            ),
        )


def _register_mobile_knowledge_tool(mcp, Context, rt) -> None:
    """Register qa_mobile_knowledge (mobile lane)."""

    @mcp.tool()
    async def qa_mobile_knowledge(  # noqa: PLR0913 - MCP tool schema
        ctx: Context,
        action: str = "list",
        package: str = "",
        item_id: str = "",
        data: dict | None = None,
    ) -> str:
        """ONE app's learned knowledge: list/show/confirm/reject/edit/export/import. data: table,page,run_id"""
        return await rt._tracked(
            "qa_mobile_knowledge",
            ctx,
            mobile_content.handle_mobile_knowledge(
                KnowledgeRequest(action, package, item_id, _data_run_id(data), data)
            ),
        )


def _register_mobile_flows_tool(mcp, Context, rt) -> None:
    """Register qa_mobile_flows (mobile lane)."""

    @mcp.tool()
    async def qa_mobile_flows(  # noqa: PLR0913 - MCP tool schema
        ctx: Context,
        action: str = "list",
        package: str = "",
        run_id: str = "",
        name: str = "",
        kind: str = "flow",
    ) -> str:
        """List, show or delete one app's saved flows."""
        return await rt._tracked(
            "qa_mobile_flows",
            ctx,
            mobile_content.handle_mobile_flows(
                mobile_content.FlowsRequest(action, package, run_id, name, kind)
            ),
        )
