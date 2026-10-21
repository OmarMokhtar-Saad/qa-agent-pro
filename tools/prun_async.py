"""The asynchronous runner leaf: ``asyncio.create_subprocess_exec`` that never raises (stdlib only)."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from typing import Any

from config.settings_guard import never_raise
from tools.prun_types import (
    ArunOpts,
    KillPlan,
    RunResult,
    _NewSessionKwargs,
    no_window_kwargs,
)


async def _spawn(args: Any, opts: ArunOpts) -> Any:
    """The started child, or the :class:`RunResult` describing the spawn failure."""
    with never_raise() as guard:
        return await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL if opts.stdin is None else opts.stdin,
            **_streams(opts.quiet),
            **(no_window_kwargs() if opts.no_window else {}),
            **_new_session_kwargs(),
        )
    exc = guard.exc
    return RunResult(None, not_found=isinstance(exc, FileNotFoundError), exc=exc)


async def _after_stop(proc: Any, stop: BaseException, opts: ArunOpts) -> RunResult:
    """Handle a timeout (kill, report) or a cancel (kill if asked, re-raise)."""
    if isinstance(stop, asyncio.TimeoutError):
        plan = KillPlan(opts.kill_drain, opts.ignore_kill_error, opts.on_drain_error)
        await _kill(proc, plan)
        return RunResult(None, timed_out=True, exc=stop)
    if opts.kill_on_cancel:
        await _kill(proc, KillPlan(True, opts.ignore_kill_error, None))
    raise stop


async def _await_result(proc: Any, timeout: Any, opts: ArunOpts) -> RunResult:
    """Wait for *proc*; on timeout, cancel or failure kill it per the legacy rules."""
    stop: BaseException | None = None
    with never_raise() as guard:
        try:
            pending = _pending(proc, opts.communicate_input, opts.stdin_data)
            stdout, stderr = await asyncio.wait_for(pending, timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            stop = exc
    if stop is not None:
        return await _after_stop(proc, stop, opts)
    if guard.failed:
        # An unexpected failure mid-wait must not leave the child running.
        await _kill(proc, KillPlan(True, True, None))
        return RunResult(None, exc=guard.exc)
    return RunResult(proc.returncode, stdout, stderr)


async def arun(args: Any, *, timeout: Any, opts: ArunOpts) -> RunResult:
    """Run *args* through ``asyncio.create_subprocess_exec``; see module docs.

    ``stdin`` is always forwarded. ``communicate_input`` picks the legacy
    ``communicate`` call form: ``True`` -> ``communicate(input=stdin_data)``
    (even when ``None``), ``False`` -> ``communicate()``. ``rc`` is the RAW
    ``proc.returncode``; delegates coerce.
    """
    proc = await _spawn(args, opts)
    if isinstance(proc, RunResult):
        return proc
    if opts.on_spawned is not None:
        opts.on_spawned()
    return await _await_result(proc, timeout, opts)


def _streams(quiet: bool) -> dict[str, Any]:
    """stdout/stderr targets for :func:`arun`: DEVNULL when *quiet*, else PIPE."""
    target = asyncio.subprocess.DEVNULL if quiet else asyncio.subprocess.PIPE
    return {"stdout": target, "stderr": target}


def _pending(proc: Any, communicate_input: bool, stdin_data: bytes | None):
    """The legacy ``communicate`` call form :func:`arun` awaits."""
    if communicate_input:
        return proc.communicate(input=stdin_data)
    return proc.communicate()


def _new_session_kwargs() -> _NewSessionKwargs:
    """Own session on POSIX so the whole process GROUP can be killed.

    Killing only the direct child leaks its grandchildren (``adb`` forks a
    server, a shell wrapper forks its command) and they keep the pipes open.
    """
    if sys.platform == "win32":
        return {}
    return {"start_new_session": True}


def _kill_now(proc: Any) -> None:
    """SIGKILL the child's process group, else just the child.

    ``ProcessLookupError`` (already gone) is swallowed: that is the goal.
    """
    pid = getattr(proc, "pid", None)
    if sys.platform != "win32" and isinstance(pid, int) and pid > 0:
        try:
            os.killpg(pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass  # not a group leader / no permission: fall back
    try:
        proc.kill()
    except ProcessLookupError:
        pass


async def _kill(proc: Any, plan: KillPlan) -> None:
    """Kill *proc*, then (bounded to 1s) drain it so it is reaped."""
    if plan.ignore_error:
        with never_raise():
            _kill_now(proc)
    else:
        _kill_now(proc)
    if not plan.drain:
        return
    with never_raise() as guard:
        await asyncio.wait_for(proc.communicate(), timeout=1)
    if isinstance(guard.exc, Exception) and plan.on_drain_error is not None:
        plan.on_drain_error(guard.exc)
