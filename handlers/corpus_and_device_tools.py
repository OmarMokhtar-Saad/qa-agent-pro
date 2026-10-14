"""MCP registration adapters: corpus_and_device_tools."""

from typing import Any

from handlers import core_tools_docs as _docs
from handlers import core_tools_run as _tool
from handlers import corpus, device_list
from handlers import corpus_device_tools_run as _run


def register_corpus_and_device_tools(mcp, kit, rt) -> None:
    """Register corpus search, Jira setup and the device/capture tools."""
    _register_search_corpus_tool(mcp, kit, rt)
    _register_configure_jira_tool(mcp, kit, rt)
    _register_device_list_tools(mcp, kit, rt)
    _register_mirror_hold_tool(mcp, kit, rt)
    _register_capture_screens_tool(mcp, kit, rt)


def _register_search_corpus_tool(mcp, kit, rt) -> None:
    """Register qa_search_corpus."""
    Context: Any = kit.context

    @_tool.tool_with_doc(mcp, _docs.SEARCH_CORPUS_DOC)
    async def qa_search_corpus(  # noqa: PLR0913 - MCP tool schema
        query: str, ctx: Context, entry_type: str = "test_case", feature: str = ""
    ) -> str:
        return await rt._tracked(
            "qa_search_corpus",
            ctx,
            corpus.handle_search_corpus(
                corpus.CorpusQuery(query, entry_type, feature),
                progress=rt._make_progress(ctx),
            ),
        )


def _register_configure_jira_tool(mcp, kit, rt) -> None:
    """Register qa_configure_jira."""
    Context: Any = kit.context

    @_tool.tool_with_doc(mcp, _docs.CONFIGURE_JIRA_DOC)
    async def qa_configure_jira(  # noqa: PLR0913 - MCP tool schema
        ctx: Context,
        base_url: str = "",
        email: str = "",
        api_token: str = "",
        atlassian_verify_json: str = "",
    ) -> str:
        return await _run.configure_jira_call(rt, locals())


def _register_device_list_tools(mcp, kit, rt) -> None:
    """Register qa_list_devices."""
    Context: Any = kit.context

    @mcp.tool()
    async def qa_list_devices(ctx: Context) -> str:
        """List attached Android/iOS devices, emulators, and simulators."""
        return await rt._tracked(
            "qa_list_devices",
            ctx,
            device_list.handle_list_devices(progress=rt._make_progress(ctx)),
        )


def _register_mirror_hold_tool(mcp, kit, rt) -> None:
    """Register qa_mirror_hold."""
    Context: Any = kit.context

    @mcp.tool()
    async def qa_mirror_hold(ctx: Context, serial: str, action: str = "status") -> str:
        """Desktop Mirror only: acquire|release|status ONE device's lock so no run drives a mirrored phone. No device command; returns JSON (owner, mirror_hold)."""

        async def _hold() -> str:
            return corpus.handle_mirror_hold(serial, action)

        return await rt._tracked("qa_mirror_hold", ctx, _hold())


# Registered UNCONDITIONALLY (not inside the full-edition block below):
# tools/device_manager IS shipped in the test-cases-only edition, capturing
# app screens GROUNDS test-case generation -- that edition's one job -- and
# this tool makes no server-side vision call and needs no credentials, so
# the credential-free promise holds. QA_MOBILE_CAPTURE was DELETED on
# 2026-08-13 (flag-surface reduction, batch 7) and hardcoded to the `true`
# the dist .env.example already shipped, so this tool is LIVE on every
# edition -- a deliberate decision, documented in docs/FEATURE_FLAGS.md and
# in the dist README's tool table. The handler's gate is now the named seam
# handlers.mobile_capture().
def _register_capture_screens_tool(mcp, kit, rt) -> None:
    """Register qa_capture_screens (every edition)."""
    Context: Any = kit.context
    ContentBlock: Any = kit.content_block

    @_tool.tool_with_doc(mcp, _docs.CAPTURE_SCREENS_DOC)
    async def qa_capture_screens(  # noqa: PLR0913 - MCP tool schema
        ctx: Context,
        device_id: str = "",
        count: int = 1,
        rescan: bool = False,
        names: str = "",
        serial: str = "",
        peek: bool = False,
    ) -> list[ContentBlock]:
        return await _run.capture_screens_call(rt, locals())
