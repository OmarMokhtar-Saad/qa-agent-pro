"""MCP registration adapters: qa_mobile_test."""

from typing import Any

from handlers.mobile import content as mobile_content

# Tool-body locals never forwarded to the handler. `rt` is a closure variable of
# the tool bodies, so locals() holds it.
_TEST_UNSENT = ("ctx", "device_id", "rt", "tester_inputs")

_INPUTS_NOTE = (
    "Note: tester_inputs is not used on this call (nothing is typed or stored "
    "here). Pass it on qa_submit_mobile_step when a packet asks for a field.\n\n"
)


def _with_inputs_note(blocks: list, tester_inputs: str) -> list:
    """Say once that ``tester_inputs`` was accepted but belongs to the step tool."""
    first = blocks[0] if blocks else None
    text = getattr(first, "text", None)
    if tester_inputs and isinstance(text, str):
        setattr(first, "text", _INPUTS_NOTE + text)
    return blocks


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
        apply: bool = True,
        dry_run: bool = False,
        continue_run: bool = False,
        serial: str = "",
        device_id: str = "",
        avd: str = "",
        new_run: bool = False,
        locale: str = "",
        capture: str = "",
        capture_ack: bool = False,
        reset_app: bool = False,
        grant_permissions: bool = True,
        launch: bool = True,
        deny_permissions: list[str] | None = None,
        charter: str = "",
        note: str = "",
        env: str = "",
        screenshot: bool = False,
        emulator: str = "",
        system_image: str = "",
        confirm_destructive: bool = False,
        tester_inputs: str = "",
    ) -> list[ContentBlock]:
        """Drive an Android device: explore toward goal=..., or run cases.
        ANY device action goes through this tool. Do NOT use raw adb/shell; it owns the destructive guard + evidence.

        Pass the task as `goal` on the FIRST call. One call does the preflight itself (device, unlock, install): no list_devices first. Install/launch/grants by default (`dry_run=true` previews); grants are test setup, not a settings change; pass `deny_permissions=["location"]` to deny one; `grant_permissions=false` only for prompt tests. Answer packets with qa_submit_mobile_step; batch ops (`tap_text_if_present` for popups). `source`: install key, `app`/`package`: its value; `device_id`: adb serial; `avd`: to boot; `charter`: JSON; new_run=true only if asked. `note`: JSON {"text","kind":wait|avoid,"when":{"op","rid"},"then":{"until_text|rid","ms"}} for THIS app.

        Report only what a qa_* observation read. If the server cannot type or act, stop and name the blocker; no raw adb, no claimed result. Never mark an unexercised check passed; ask before skipping a step. Prove the destination, not only text_absent. Relay a finished run's verdict verbatim; device released: no status/stop. Never guess app/package/device."""
        fields = {k: v for k, v in locals().items() if k not in _TEST_UNSENT}
        fields["serial"] = rt._device_ref(device_id, serial)
        blocks = await rt._mobile_reply(
            rt.MobileReplySpec(
                "qa_mobile_test", mobile_content.handle_mobile_test_content
            ),
            ctx,
            fields,
            **rt._make_elicitors(ctx),
        )
        return _with_inputs_note(blocks, tester_inputs)
