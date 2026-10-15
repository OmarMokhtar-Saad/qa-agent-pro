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

        ANY Android device action goes through this tool. Do NOT use raw adb or shell: it owns the destructive guard, run folder and evidence.

        Pass the task as `goal` on the FIRST call. One call does the preflight itself (device pick, unlock, install source): no list_devices/status first unless asked. Install/launch, `locale`, `reset_app` need apply=true. Answer packets with qa_submit_mobile_step: taps settle; batch ops (fill, fill, tap; `tap_text_if_present` for a popup). `source`: install key, `app`/`package`: its value; `device_id`: adb serial; `avd`: to boot; `charter`: JSON, no secrets; new_run=true only if asked. `note`: JSON {"text","kind":wait|avoid,"when":{"op","rid"},"then":{"until_text|rid","ms"}} for THIS app.

        Report only what a qa_* observation read. If the server cannot type or act, stop and name the blocker; no raw adb, no claimed result. Never mark an unexercised check passed; ask before skipping a step of the goal. Prove the destination positively, not only text_absent. Relay a finished run's verdict verbatim; run over, device released: no status/stop. Ask, never guess, app, package or device."""
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
