"""Spawning ``adb`` and timing it: the resolver and ``run_argv`` (moved from adb.py)."""

from __future__ import annotations

import asyncio
import logging
import time

from tools.mobile import adb_sock, dump_reuse, step_timing
from tools.mobile.adb_host import host
from tools.process_runner import ArunOpts, arun

logger = logging.getLogger("tools.mobile.adb")


def resolve_adb() -> str:
    """One resolver for the lane, the device list and qa-doctor.

    Delegates at call time to :func:`tools.mobile.sdk_locator.resolve_adb`
    rather than aliasing it, so patching either name reaches every caller.
    """
    return host().sdk_locator.resolve_adb()


def _cmd_for_log(cmd: list[str], stdin_data: bytes | None = None) -> str:
    """The command line to log in full -- no argv element is ever a secret
    (see the module docstring); the ONE channel that carries typed text or
    credentials is ``stdin_data``, so it is logged as a byte count, never its
    content.
    """
    rendered = " ".join(cmd)
    if stdin_data is not None:
        rendered += f" <stdin:{len(stdin_data)}B>"
    return rendered


async def _via_socket(cmd: list[str], timeout: float, stdin: bytes | None):
    """The socket's result and spawn clock for *cmd*, or ``None`` for the subprocess path."""
    args = (cmd,) if stdin is None else (cmd, stdin)
    if not adb_sock.applicable(*args):
        return None
    clock = [host().time.monotonic()]
    r = await adb_sock.try_run(cmd, timeout, stdin)
    if r is None:
        logger.debug("mobile.adb: socket fault, subprocess for %ss", adb_sock.RETRY_S)
        return None
    return r, clock


async def _subprocess(cmd: list[str], timeout: float, stdin_data: bytes | None):
    # A single case makes dozens of adb calls. Without the no-window flag each
    # one flashes a console window on Windows, which reads as a broken editor.
    clock: list[float] = []
    r = await arun(
        cmd,
        timeout=timeout,
        opts=ArunOpts(
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
            communicate_input=True,
            stdin_data=stdin_data,
            no_window=True,
            kill_drain=True,
            kill_on_cancel=True,
            ignore_kill_error=False,
            on_drain_error=None,
            on_spawned=lambda: clock.append(host().time.monotonic()),
        ),
    )
    return r, clock


async def _spawn(cmd: list[str], timeout: float, stdin_data: bytes | None):
    via = await _via_socket(cmd, timeout, stdin_data)
    if via is not None:
        return via
    return await _subprocess(cmd, timeout, stdin_data)


def _warn_timed_out(cmd: list[str], timeout: float, stdin_data: bytes | None) -> None:
    logger.warning(
        "mobile.adb: command timed out after %ss: %s",
        timeout,
        _cmd_for_log(cmd, stdin_data),
    )


async def _spawn_noting(cmd: list[str], timeout: float, stdin: bytes | None):
    """``_spawn``, ending any held dump before and after an actuating command."""
    dump_reuse.note_command(cmd)
    try:
        return await _spawn(cmd, timeout, stdin)
    finally:
        dump_reuse.note_command(cmd)


async def _spawn_timed(cmd: list[str], timeout: float, stdin: bytes | None):
    """``_spawn_noting``, its wall time ledgered as ``adb`` unless it is a dump
    (the dump is timed, and named, by its own phase)."""
    run = _spawn_noting(cmd, timeout, stdin)
    if dump_reuse.body_of(cmd).startswith("uiautomator dump"):
        return await run
    start = time.monotonic()
    out = await step_timing.timed("adb", run)
    step_timing.adb_cost(dump_reuse.body_of(cmd), time.monotonic() - start)
    return out


async def run_argv(
    cmd: list[str], timeout: float, stdin_data: bytes | None = None
) -> tuple[int, bytes, bytes]:
    """``device_manager._run``'s contract, plus optional stdin.

    Raises ``FileNotFoundError`` when adb is missing and
    ``asyncio.TimeoutError`` on overrun; both are converted by the callers
    below, so nothing raises past this module.
    """
    r, clock = await _spawn_timed(cmd, timeout, stdin_data)
    if r.timed_out:
        _warn_timed_out(cmd, timeout, stdin_data)
    if r.exc is not None:
        raise r.exc
    elapsed = host().time.monotonic() - clock[0]
    host()._log_call_duration(elapsed, _cmd_for_log(cmd, stdin_data))
    return int(r.rc or 0), r.stdout or b"", r.stderr or b""


def _log_call_duration(duration: float, rendered_cmd: str) -> None:
    """Warn on a call over the slow-call ceiling, debug-log any other."""
    ceiling = host().SLOW_CALL_CEILING_S
    if duration > ceiling:
        logger.warning(
            "mobile.adb: call took %.2fs (over %ss): %s",
            duration,
            ceiling,
            rendered_cmd,
        )
    else:
        logger.debug("mobile.adb: call took %.2fs: %s", duration, rendered_cmd)
