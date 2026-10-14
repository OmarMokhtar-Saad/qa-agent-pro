"""Subprocess plumbing for ``tools.device_manager``: the one place ``arun`` is called."""

from __future__ import annotations

import asyncio
import logging
from typing import cast

from tools import settings_port
from tools.device_manager_host import host
from tools.process_runner import ArunOpts, arun

logger = logging.getLogger("tools.device_manager")


def _cmd_timeout() -> int:
    return settings_port.get().qa_device_command_timeout


def _shot_timeout() -> int:
    return settings_port.get().qa_device_screenshot_timeout


def _drain_failed(_exc: Exception) -> None:
    # The kill is what matters; draining after it is courtesy. Logged
    # rather than passed so a device that cannot be reaped leaves a
    # trace -- the warning below only says the command timed out.
    logger.debug("device_manager: drain after kill failed", exc_info=True)


async def _arun_cmd(cmd: list[str], timeout: int):
    return await arun(
        cmd,
        timeout=timeout,
        opts=ArunOpts(
            stdin=asyncio.subprocess.DEVNULL,
            communicate_input=False,
            kill_drain=True,
            kill_on_cancel=True,
            ignore_kill_error=False,
            on_drain_error=_drain_failed,
        ),
    )


async def _arun_start_server(binary: str):
    """``arun`` of the quiet ``adb start-server``: ``(result, spawned)``."""
    spawned: list[bool] = []
    r = await arun(
        [binary, "start-server"],
        timeout=host().ADB_START_SERVER_TIMEOUT_S,
        opts=ArunOpts(
            stdin=asyncio.subprocess.DEVNULL,
            quiet=True,
            kill_drain=False,
            ignore_kill_error=True,
            on_spawned=lambda: spawned.append(True),
        ),
    )
    return r, spawned


def _log_duration(cmd: list[str], duration: float) -> None:
    ceiling = host().SLOW_CALL_CEILING_S
    if duration > ceiling:
        logger.warning(
            "device_manager: call took %.2fs (over %ss): %s",
            duration,
            ceiling,
            " ".join(cmd),
        )
    else:
        logger.debug("device_manager: call took %.2fs: %s", duration, " ".join(cmd))


# Run *cmd* (argument list, NO shell) and return (returncode, stdout, stderr).
#
# Raises ``FileNotFoundError`` when the binary is missing (callers convert this
# to a friendly error) and ``asyncio.TimeoutError`` when the call overruns
# *timeout*.
#
# ``started`` is taken BEFORE the spawn so a slow exec counts toward the
# duration line, and stdin is DEVNULL so a child that reads stdin cannot hang
# on the server's own (MCP stdio) pipe.
async def run_cmd(cmd: list[str], timeout: int) -> tuple[int, bytes, bytes]:
    """Run *cmd* (argument list, NO shell): (returncode, stdout, stderr)."""
    started = host().time.monotonic()
    r = await _arun_cmd(cmd, timeout)
    if r.timed_out:
        logger.warning(
            "device_manager: command timed out after %ss: %s", timeout, " ".join(cmd)
        )
    if r.exc is not None:
        raise r.exc
    _log_duration(cmd, host().time.monotonic() - started)
    return r.rc or 0, r.stdout or b"", r.stderr or b""


async def _checked_run(cmd: list[str], timeout: int, missing: str = ""):
    """``run_cmd`` that maps a missing binary / timeout to ``None`` (never raises those)."""
    try:
        return await host()._run(cmd, timeout)
    except FileNotFoundError:
        if missing:
            logger.info(missing)
        return None
    except asyncio.TimeoutError:
        return None


def _rc_ok(got: tuple, msg: str, level: int) -> bool:
    """True when the run succeeded; else log *msg* with stderr and return False."""
    rc, _out, err = got
    if rc != 0:
        logger.log(level, msg, err.decode(errors="replace")[:200])
    return rc == 0


def _rc_zero(got: tuple, msg: str, level: int) -> bool:
    """True when the run succeeded; else log *msg* with the return code."""
    rc = got[0]
    if rc != 0:
        logger.log(level, msg, rc)
    return rc == 0


def _unpack_device(device: dict | None) -> tuple[str, str, str]:
    """``(device_id, platform, kind)`` of a device dict, platform/kind lower-cased."""
    device = device or {}
    device_id = cast("str", device.get("id"))
    return (
        device_id,
        (device.get("platform") or "").lower(),
        (device.get("kind") or "").lower(),
    )


def _error_reply(msg: str) -> dict:
    return {"error": msg, "content": None}
