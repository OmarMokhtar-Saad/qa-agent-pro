"""MCP registration adapters: the mobile device tools (stop, app info, update)."""

from handlers.mobile import device as mobile_device
from handlers.mobile.device_requests import AppArgs, UpdateArgs


def _register_mobile_stop_tool(mcp, Context, rt) -> None:
    """Register qa_mobile_stop (mobile lane)."""

    @mcp.tool()
    async def qa_mobile_stop(ctx: Context, target: str = "") -> str:
        """Stop ONE mobile run and free its device. `target`: run id or device serial. Do not guess a run or device: ask the user."""
        return await rt._tracked(
            "qa_mobile_stop",
            ctx,
            mobile_device.handle_mobile_stop(target, **rt._make_elicitors(ctx)),
        )


def _register_mobile_app_info_tool(mcp, Context, rt) -> None:
    """Register qa_app_info (mobile lane)."""

    @mcp.tool()
    async def qa_app_info(  # noqa: PLR0913 - MCP tool schema
        ctx: Context, app: str = "", package: str = "", device_id: str = ""
    ) -> str:
        """Read an installed app's version on a device. Touches nothing. Do not guess an app, package or device: ask the user."""
        return await rt._tracked(
            "qa_app_info",
            ctx,
            mobile_device.handle_app_info(
                AppArgs(app, package, device_id), **rt._make_elicitors(ctx)
            ),
        )


def _register_mobile_update_app_tool(mcp, Context, rt) -> None:
    """Register qa_update_app (mobile lane)."""

    @mcp.tool()
    async def qa_update_app(  # noqa: PLR0913 - MCP tool schema
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
                UpdateArgs(app, package, device_id, source), **rt._make_elicitors(ctx)
            ),
        )
