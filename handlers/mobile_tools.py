"""MCP registration adapters: mobile_tools."""

from handlers import _common, mobile_content, mobile_device


def register_mobile_tools(mcp, Context, ContentBlock, rt) -> None:
    """Register the mobile emulator lane tools when its modules are present."""
    # The mobile emulator lane. ONE call, and nothing else may join it here:
    # `_mobile_lane_enabled()` is `_mobile_modules_present()` alone, which
    # checks tools/mobile is really on disk -- a build made without the pinned
    # IME ships none of it. QA_MOBILE_HTTPS_CAPTURE_ENABLED is not a term: it gates only
    # the capture download and the capture-certificate install, at those effects.
    # tests/mobile/test_mobile_registration.py parses this file and fails if
    # this `if` becomes anything other than a call to that predicate.
    #
    # It carried a THIRD term, `not _test_cases_only()`, until 2026-09-04. That
    # is an edition label decided by whether the bug-report and coach agents
    # shipped, and it made the lane unreachable on every distribution build:
    # v1.77.0 registered none of these three tools while its own README
    # promised all three. Do not re-add it here or in the predicate.
    if _common._mobile_lane_enabled():
        _register_mobile_device_tools(mcp, Context, rt)
        _register_mobile_test_tool(mcp, Context, ContentBlock, rt)
        _register_mobile_submit_step_tool(mcp, Context, ContentBlock, rt)
        _register_mobile_status_tools(mcp, Context, rt)
        _register_mobile_knowledge_tools(mcp, Context, rt)
        _register_mobile_flows_tool(mcp, Context, rt)
        _register_mobile_capture_setup_tool(mcp, Context, rt)


# Tool-body locals never forwarded to the handler. `rt` is a closure variable of
# the tool bodies, so locals() holds it.
_TEST_UNSENT = ("ctx", "device_id", "rt")
_STEP_UNSENT = ("ctx", "rt")


def _register_mobile_device_tools(mcp, Context, rt) -> None:
    """Register qa_mobile_stop, qa_app_info and qa_update_app (mobile lane)."""

    @mcp.tool()
    async def qa_mobile_stop(ctx: Context, target: str = "") -> str:
        """Stop ONE mobile run and free its device. `target`: run id or device serial. Do not guess a run or device: ask the user."""
        return await rt._tracked(
            "qa_mobile_stop",
            ctx,
            mobile_device.handle_mobile_stop(target, **rt._make_elicitors(ctx)),
        )

    @mcp.tool()
    async def qa_app_info(
        ctx: Context, app: str = "", package: str = "", device_id: str = ""
    ) -> str:
        """Read an installed app's version on a device. Touches nothing. Do not guess an app, package or device: ask the user."""
        return await rt._tracked(
            "qa_app_info",
            ctx,
            mobile_device.handle_app_info(
                app, package, device_id, **rt._make_elicitors(ctx)
            ),
        )

    @mcp.tool()
    async def qa_update_app(
        ctx: Context,
        app: str = "",
        package: str = "",
        device_id: str = "",
        source: str = "",
    ) -> str:
        """Update an app on a device from an install `source`; a downgrade asks before uninstalling. Do not guess an app, package, device or source: ask the user."""
        return await rt._tracked(
            "qa_update_app",
            ctx,
            mobile_device.handle_update_app(
                app, package, device_id, source, **rt._make_elicitors(ctx)
            ),
        )


def _register_mobile_test_tool(mcp, Context, ContentBlock, rt) -> None:
    """Register qa_mobile_test (mobile lane)."""

    @mcp.tool()
    async def qa_mobile_test(
        ctx: Context,
        source: str = "",
        suite_id: str = "",
        goal: str = "",
        cases: str = "",
        app: str = "",
        package: str = "",
        run_id: str = "",
        session_token: str = "",
        apply: bool = False,
        continue_run: bool = False,
        serial: str = "",
        device_id: str = "",
        avd: str = "",
        new_run: bool = False,
        locale: str = "",
        capture: str = "",
        capture_ack: bool = False,
        reset_app: bool = False,
        charter: str = "",
        note: str = "",
        env: str = "",
        screenshot: bool = False,
        emulator: str = "",
        system_image: str = "",
        confirm_destructive: bool = False,
    ) -> list[ContentBlock]:
        """Drive an Android device: ad-hoc steps (goal=...), cases, exploration.

        ANY Android device action goes through this tool. Do NOT use raw adb or shell: this tool owns the destructive guard, run folder and evidence.

        Call with NO arguments to start. Install/download/launch needs apply=true. run_id continues a run in ANY chat. ONE packet at a time; answer each with qa_submit_mobile_step. Install key in `source`, its value in `app` (or `package`); `device_id`: an adb serial; `avd`: one to boot; `charter`: JSON, no secrets; `locale`, `reset_app=true` need apply=true; new_run=true only if the tester asks. `note`: JSON {"text","kind":wait|avoid,"when":{"op","rid"},"then":{"until_text|rid","ms"}} for THIS app; no secrets.

        Never report a screen state, field value or login outcome that was not read from a qa_* observation. If the server cannot type or act, stop and report the blocker by name. Do not fall back to raw adb input, and do not claim a result. Relay a finished run's verdict block word for word. Ask, never guess, the app, package, device or source."""
        # Every parameter except ctx/device_id goes to the handler by name.
        fields = {k: v for k, v in locals().items() if k not in _TEST_UNSENT}
        fields["serial"] = rt._device_ref(device_id, serial)
        return await rt._mobile_reply(
            "qa_mobile_test",
            ctx,
            mobile_content.handle_mobile_test_content,
            fields,
            **rt._make_elicitors(ctx),
        )


def _register_mobile_submit_step_tool(mcp, Context, ContentBlock, rt) -> None:
    """Register qa_submit_mobile_step (mobile lane)."""

    @mcp.tool()
    @rt._with_script_doc
    async def qa_submit_mobile_step(
        run_id: str,
        ctx: Context,
        tc_id: str = "",
        script: str = "",
        tester_input: str = "",
        tester_input_field: str = "",
        session_token: str = "",
        confirm_destructive: bool = False,
        tester_inputs: str = "",
        note: str = "",
        env: str = "",
        screenshot: bool = False,
        flow: str = "",
        flow_params: str = "",
        save_flow: str = "",
        route: str = "",
        save_route: str = "",
        finding: str = "",
    ) -> list[ContentBlock]:
        """Submit the action script YOU planned for a mobile packet.

        The reply is the verdict plus the NEXT packet. For a credential, ask the TESTER for that field and pass it as tester_input with tester_input_field (several: tester_inputs='{"login_password": "...", "login_otp": "..."}'); it is typed into the app and stored nowhere. Pass confirm_destructive=true only after the TESTER confirms a guard stop.

        `note`: as on `qa_mobile_test`. Never report a screen state, field value or login outcome that was not read from a qa_* observation. If the server cannot type or act, stop and report the blocker by name. Do not fall back to raw adb input, and do not claim a result. `finding`: explore runs only, one observation per turn."""
        fields = {k: v for k, v in locals().items() if k not in _STEP_UNSENT}
        return await rt._mobile_reply(
            "qa_submit_mobile_step",
            ctx,
            mobile_content.handle_submit_mobile_step_content,
            fields,
        )


def _register_mobile_status_tools(mcp, Context, rt) -> None:
    """Register qa_mobile_status and qa_network_watch (mobile lane)."""

    @mcp.tool()
    async def qa_mobile_status(
        ctx: Context,
        run_id: str = "",
        session_token: str = "",
        report_now: bool = False,
    ) -> str:
        """Where a mobile run stands, read from disk. Touches no device. Device actions go through `qa_mobile_test` (goal=... for ad-hoc steps), never raw adb.

        Emulator, lease holder, cases done/failed/remaining. Call after anything outliving a tool call (big install, cold boot). No run_id lists runs; report_now=true writes the HTML report and returns its path."""
        return await rt._tracked(
            "qa_mobile_status",
            ctx,
            mobile_device.handle_mobile_status(
                run_id, session_token, report_now, progress=rt._make_progress(ctx)
            ),
        )

    # Registered under the mobile-lane gate, deliberately: a network watch
    # drives the emulator console and samples the device's socket table, so
    # it lives or dies with `_mobile_lane_enabled()` exactly like the three
    # tools beside it. No flag: like the rest of the lane it runs wherever
    # the modules are on disk.
    @mcp.tool()
    async def qa_network_watch(
        ctx: Context,
        action: str = "status",
        serial: str = "",
        package: str = "",
        apply: bool = False,
        device_id: str = "",
    ) -> str:
        """Watch what an emulator puts on the wire, passively, root-free. `device_id`: adb serial (alias `serial`). `action="start"` needs `apply=true`; `"stop"` (REVERTS) never does; `"status"`. Hosts come from TLS ClientHello and DNS; **paths and query strings are not visible and never guessed**. The pcap is parsed and DELETED; only the summary returns."""
        return await rt._tracked_noted(
            "qa_network_watch",
            ctx,
            mobile_device.handle_network_watch(
                action, rt._device_ref(device_id, serial), package, apply
            ),
        )


def _register_mobile_knowledge_tools(mcp, Context, rt) -> None:
    """Register qa_mobile_notes and qa_mobile_knowledge (mobile lane)."""

    @mcp.tool()
    async def qa_mobile_notes(
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
                action, package, run_id, note_id, reason
            ),
        )

    @mcp.tool()
    async def qa_mobile_knowledge(
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
                action,
                package,
                item_id,
                str((data or {}).get("run_id") or ""),
                data,
            ),
        )


def _register_mobile_flows_tool(mcp, Context, rt) -> None:
    """Register qa_mobile_flows (mobile lane)."""

    @mcp.tool()
    async def qa_mobile_flows(
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
            mobile_content.handle_mobile_flows(action, package, run_id, name, kind),
        )


def _register_mobile_capture_setup_tool(mcp, Context, rt) -> None:
    """Register qa_setup_capture (mobile lane)."""

    @mcp.tool()
    async def qa_setup_capture(
        ctx: Context,
        serial: str = "",
        action: str = "prepare",
        apply: bool = False,
        capture_ack: bool = False,
        device_id: str = "",
    ) -> str:
        """Prepare, check or remove API capture on a device AHEAD of a run. `device_id` is the adb serial (`serial` is an alias). `action="prepare"` installs the qa-agents proxy certificate and proves decryption; `"status"` reads the device's trust from disk; `"remove"` clears the live proxy. prepare and remove need `apply=true`."""
        return await rt._tracked_noted(
            "qa_setup_capture",
            ctx,
            mobile_device.handle_setup_capture(
                rt._device_ref(device_id, serial), action, apply, capture_ack
            ),
        )
