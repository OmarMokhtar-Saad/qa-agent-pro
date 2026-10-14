"""MCP registration adapters: qa_mobile_test."""

from typing import Any

from handlers.mobile import content as mobile_content

# Tool-body locals never forwarded to the handler. `rt` is a closure variable of
# the tool bodies, so locals() holds it.
_TEST_UNSENT = ("ctx", "device_id", "rt")


def _register_mobile_test_tool(mcp, kit, rt) -> None:
    """Register qa_mobile_test (mobile lane)."""
    Context: Any = kit.context
    ContentBlock: Any = kit.content_block

    @mcp.tool()
    async def qa_mobile_test(  # noqa: PLR0913 - MCP tool schema
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
            rt.MobileReplySpec(
                "qa_mobile_test", mobile_content.handle_mobile_test_content
            ),
            ctx,
            fields,
            **rt._make_elicitors(ctx),
        )
