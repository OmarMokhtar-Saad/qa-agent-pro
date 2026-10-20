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
        """Submit the action script YOU planned for the current mobile packet; the reply is the verdict plus the NEXT packet. `script`: {"actions":[...]}, at most 10 per step (ops and selectors: MOBILE RULES in the first reply). Credential: ask the TESTER, pass tester_input + tester_input_field. `finding`: explore runs only."""
        fields = {k: v for k, v in locals().items() if k not in _STEP_UNSENT}
        return await rt._mobile_reply(
            rt.MobileReplySpec(
                "qa_submit_mobile_step",
                mobile_content.handle_submit_mobile_step_content,
            ),
            ctx,
            fields,
        )
