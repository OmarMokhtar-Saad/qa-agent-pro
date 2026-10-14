"""MCP registration adapters: qa_submit_mobile_step."""

from typing import Any

from handlers.mobile import content as mobile_content

# Tool-body locals never forwarded to the handler. `rt` is a closure variable of
# the tool bodies, so locals() holds it.
_STEP_UNSENT = ("ctx", "rt")


def _register_mobile_submit_step_tool(mcp, kit, rt) -> None:
    """Register qa_submit_mobile_step (mobile lane)."""
    Context: Any = kit.context
    ContentBlock: Any = kit.content_block

    @mcp.tool()
    @rt._with_script_doc
    async def qa_submit_mobile_step(  # noqa: PLR0913 - MCP tool schema
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
            rt.MobileReplySpec(
                "qa_submit_mobile_step",
                mobile_content.handle_submit_mobile_step_content,
            ),
            ctx,
            fields,
        )
