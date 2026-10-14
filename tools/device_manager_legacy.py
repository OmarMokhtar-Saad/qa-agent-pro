"""Private legacy seams of ``tools.device_manager`` (WX removes them).

conftest and foreign tests still patch ``_run`` / ``_start_adb_server`` on the
facade. Each calls the public seam at call time, so a patch of either name bites.
"""

from __future__ import annotations

from tools.device_manager_host import host


async def _run(cmd: list[str], timeout: int) -> tuple[int, bytes, bytes]:
    return await host().run_cmd(cmd, timeout)


async def _start_adb_server() -> str | None:
    return await host().start_adb_server()
