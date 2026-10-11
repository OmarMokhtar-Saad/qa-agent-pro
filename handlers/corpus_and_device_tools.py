"""MCP registration adapters: corpus_and_device_tools."""

from handlers import capture, corpus, jira, mobile_device


def register_corpus_and_device_tools(mcp, Context, ContentBlock, rt) -> None:
    """Register corpus search, Jira setup and the device/capture tools."""

    @mcp.tool()
    async def qa_search_corpus(
        query: str, ctx: Context, entry_type: str = "test_case", feature: str = ""
    ) -> str:
        """Search the RAG corpus for similar past test cases or bug reports. entry_type is 'test_case' or 'bug_report'; pass
        feature to narrow results to entries stored for that feature."""
        return await rt._tracked(
            "qa_search_corpus",
            ctx,
            corpus.handle_search_corpus(
                query, entry_type, feature, progress=rt._make_progress(ctx)
            ),
        )

    @mcp.tool()
    async def qa_configure_jira(
        ctx: Context,
        base_url: str = "",
        email: str = "",
        api_token: str = "",
        atlassian_verify_json: str = "",
    ) -> str:
        """DEPRECATED: Jira needs no credentials here; tickets are read through YOUR OWN Atlassian MCP connection. This tool returns connection steps and stores NOTHING; never ask for an API token. If a ticket URL fails, call qa_prepare_test_cases and follow its directive. To VERIFY the connection, call with no arguments (it directs you to mcp__atlassian__atlassianUserInfo), then again with atlassian_verify_json set to that RAW JSON result (or {"error": "..."})."""
        return await rt._tracked_noted(
            "qa_configure_jira",
            ctx,
            jira.handle_configure_jira(
                base_url,
                email,
                api_token,
                atlassian_verify_json=atlassian_verify_json,
                progress=rt._make_progress(ctx),
            ),
        )

    _register_device_list_tools(mcp, Context, rt)
    _register_capture_screens_tool(mcp, Context, ContentBlock, rt)


def _register_device_list_tools(mcp, Context, rt) -> None:
    """Register qa_list_devices and qa_mirror_hold."""

    @mcp.tool()
    async def qa_list_devices(ctx: Context) -> str:
        """List attached Android/iOS devices, emulators, and simulators."""
        return await rt._tracked(
            "qa_list_devices",
            ctx,
            mobile_device.handle_list_devices(progress=rt._make_progress(ctx)),
        )

    @mcp.tool()
    async def qa_mirror_hold(ctx: Context, serial: str, action: str = "status") -> str:
        """Desktop Mirror only: acquire|release|status ONE device's lock so no run drives a mirrored phone. No device command; returns JSON (owner, mirror_hold)."""

        async def _hold() -> str:
            return corpus.handle_mirror_hold(serial, action)

        return await rt._tracked("qa_mirror_hold", ctx, _hold())


def _register_capture_screens_tool(mcp, Context, ContentBlock, rt) -> None:
    """Register qa_capture_screens (every edition)."""

    # Registered UNCONDITIONALLY (not inside the full-edition block below):
    # tools/device_manager IS shipped in the test-cases-only edition, capturing
    # app screens GROUNDS test-case generation -- that edition's one job -- and
    # this tool makes no server-side vision call and needs no credentials, so
    # the credential-free promise holds. QA_MOBILE_CAPTURE was DELETED on
    # 2026-08-13 (flag-surface reduction, batch 7) and hardcoded to the `true`
    # the dist .env.example already shipped, so this tool is LIVE on every
    # edition -- a deliberate decision, documented in docs/FEATURE_FLAGS.md and
    # in the dist README's tool table. The handler's gate is now the named seam
    # handlers._mobile_capture().
    @mcp.tool()
    async def qa_capture_screens(
        ctx: Context,
        device_id: str = "",
        count: int = 1,
        rescan: bool = False,
        names: str = "",
        serial: str = "",
        peek: bool = False,
    ) -> list[ContentBlock]:
        """Capture screenshots from a connected phone/emulator/simulator: images PLUS one capture_id per screen. Use it to ground test cases in REAL screens, especially for a Jira ticket (its images are unreadable here): pass the `capture_ids` to `qa_prepare_test_cases`/`qa_generate_test_cases`. `count` = screens. `peek`=true: text. `names` (comma-separated) ONLY if the user told you; never ask. `serial` aliases `device_id`; omit it for a picker; ids expire after 30 minutes."""
        from mcp.types import TextContent

        text, specs = await rt._tracked(
            "qa_capture_screens",
            ctx,
            capture.handle_capture_screens(
                device_id=rt._device_ref(device_id, serial),
                count=count,
                rescan=rescan,
                names=names,
                peek=peek,
                **rt._make_elicitors(ctx),
                progress=rt._make_progress(ctx),
            ),
        )
        return [TextContent(type="text", text=text), *rt._image_content_blocks(specs)]
