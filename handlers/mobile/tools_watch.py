"""MCP registration adapters: the mobile status, network-watch and capture-setup tools."""

from handlers.mobile import device as mobile_device
from handlers.mobile.device_requests import CapArgs, StatusArgs, WatchArgs


def _register_mobile_status_tool(mcp, Context, rt) -> None:
    """Register qa_mobile_status (mobile lane)."""

    @mcp.tool()
    async def qa_mobile_status(  # noqa: PLR0913 - MCP tool schema
        ctx: Context,
        run_id: str = "",
        session_token: str = "",
        report_now: bool = False,
    ) -> str:
        """Where a mobile run stands, read from disk; touches no device. No run_id lists runs; report_now=true writes the HTML report. Device actions go through `qa_mobile_test`, never raw adb."""
        return await rt._tracked_mobile(
            "qa_mobile_status",
            ctx,
            mobile_device.handle_mobile_status(
                StatusArgs(run_id, session_token, report_now, rt._make_progress(ctx))
            ),
        )


# Registered under the mobile-lane gate, deliberately: a network watch
# drives the emulator console and samples the device's socket table, so
# it lives or dies with `mobile_lane_enabled()` exactly like the three
# tools beside it. No flag: like the rest of the lane it runs wherever
# the modules are on disk.
def _register_mobile_network_watch_tool(mcp, Context, rt) -> None:
    """Register qa_network_watch (mobile lane)."""

    @mcp.tool()
    async def qa_network_watch(  # noqa: PLR0913 - MCP tool schema
        ctx: Context,
        action: str = "status",
        serial: str = "",
        package: str = "",
        apply: bool = False,
        device_id: str = "",
    ) -> str:
        """Watch what an emulator puts on the wire, passively, root-free. `device_id`: adb serial. `action="start"` needs `apply=true`; `"stop"` (REVERTS) never does; `"status"`. Hosts only (TLS ClientHello, DNS); paths are never guessed. Only the summary returns."""
        return await rt._tracked_mobile_noted(
            "qa_network_watch",
            ctx,
            mobile_device.handle_network_watch(
                WatchArgs(action, rt._device_ref(device_id, serial), package, apply),
            ),
        )


def _register_mobile_capture_setup_tool(mcp, Context, rt) -> None:
    """Register qa_setup_capture (mobile lane)."""

    @mcp.tool()
    async def qa_setup_capture(  # noqa: PLR0913 - MCP tool schema
        ctx: Context,
        serial: str = "",
        action: str = "prepare",
        apply: bool = False,
        capture_ack: bool = False,
        device_id: str = "",
    ) -> str:
        """Prepare, check or remove API capture on a device AHEAD of a run. `device_id`: adb serial. `action`: "prepare" (installs the proxy certificate, proves decryption), "status", "remove". prepare and remove need `apply=true`."""
        return await rt._tracked_mobile_noted(
            "qa_setup_capture",
            ctx,
            mobile_device.handle_setup_capture(
                CapArgs(rt._device_ref(device_id, serial), action, apply, capture_ack),
            ),
        )
