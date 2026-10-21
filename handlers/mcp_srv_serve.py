"""Run the stdio server with the event-loop stall detector beside it."""

from __future__ import annotations

from typing import Any

import anyio

from handlers import _lane
from handlers.mcp_srv_parent import watch_parent
from tools.loop_stall import watch_loop


async def _serve(server: Any) -> None:
    async with anyio.create_task_group() as tg:
        tg.start_soon(watch_loop)
        tg.start_soon(watch_parent)
        if _lane.mobile_lane is not None:
            tg.start_soon(_lane.mobile_lane.close_orphans)
        await server.run_async(show_banner=False)
        tg.cancel_scope.cancel()


def run_server(server: Any) -> None:
    """Blocking replacement for ``server.run(show_banner=False)``."""
    anyio.run(_serve, server)
