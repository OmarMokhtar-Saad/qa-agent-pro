"""The bodies of the Jira-setup and capture MCP tool wrappers."""

from typing import Any

from mcp.types import TextContent

from handlers import capture, jira


async def configure_jira_call(rt: Any, loc: dict[str, Any]) -> str:
    """Run qa_configure_jira; ``loc`` holds the wrapper locals, picked by key."""
    ctx = loc["ctx"]
    args = jira.ConfigureJiraArgs(
        loc["base_url"],
        loc["email"],
        loc["api_token"],
        loc["atlassian_verify_json"],
    )
    return await rt._tracked_noted(
        "qa_configure_jira",
        ctx,
        jira.handle_configure_jira(args, progress=rt._make_progress(ctx)),
    )


async def capture_screens_call(rt: Any, loc: dict[str, Any]) -> Any:
    """Run qa_capture_screens; ``loc`` holds the wrapper locals, picked by key."""

    ctx = loc["ctx"]
    text, specs = await rt._tracked(
        "qa_capture_screens",
        ctx,
        capture.handle_capture_screens(
            device_id=rt._device_ref(loc["device_id"], loc["serial"]),
            count=loc["count"],
            rescan=loc["rescan"],
            names=loc["names"],
            peek=loc["peek"],
            **rt._make_elicitors(ctx),
            progress=rt._make_progress(ctx),
        ),
    )
    return [TextContent(type="text", text=text), *rt._image_content_blocks(specs)]
