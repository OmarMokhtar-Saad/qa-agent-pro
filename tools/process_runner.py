"""One subprocess runner for the legacy ``_run`` helpers (stdlib only, a leaf).

Four modules each hand-rolled the same spawn / wait / kill dance with small,
test-pinned differences. This module holds the shared core; every legacy
``_run`` stays a thin, patchable delegate that maps the :class:`RunResult` back
to its exact old contract (return tuple, raised exception, log text).

Rules the core keeps so that a delegate is byte-for-byte the old behaviour:

* A keyword reaches ``subprocess.run`` / ``asyncio.create_subprocess_exec`` /
  ``communicate`` only where the legacy site passed it. ``None`` means "not
  passed"; nothing is added for safety.
* The stdlib is called through module attributes (``subprocess.run``,
  ``asyncio.create_subprocess_exec``, ``asyncio.wait_for``) so a test that
  patches those shared objects still intercepts.
* The core never logs and never reads a clock; delegates own both.
* The core returns failures in :class:`RunResult` instead of raising, with
  exactly two intentional raises, both legacy-preserving: ``CancelledError``
  after the kill (``kill_on_cancel``), and anything a caller-supplied hook
  (``on_spawned`` / ``on_drain_error``) raises. A kill of an already-dead child
  (``ProcessLookupError``) is swallowed. ``arun`` starts the child in its own
  session and kills the whole process group, so grandchildren die with it.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

#: Windows process-creation flag that keeps a child's console hidden. Named here
#: rather than imported from ``subprocess`` because that attribute does not exist
#: on POSIX, so a monkeypatched ``sys.platform`` would make the lookup raise.
CREATE_NO_WINDOW = 0x08000000


def no_window_kwargs() -> dict:
    """``Popen`` keywords that keep a SYNCHRONOUS child's console hidden.

    ``detach_kwargs`` already carries ``CREATE_NO_WINDOW`` for the three
    detached spawns, but every SYNCHRONOUS child -- the virtualization probe,
    ``sdkmanager``, ``avdmanager`` and every one of the dozens of ``adb`` calls a
    single case makes -- had no creation flags at all, so on Windows each one
    flashes a console window in the tester's face.

    An empty dict on POSIX rather than ``creationflags=0``: POSIX
    ``subprocess`` tolerates the zero, but ``asyncio.create_subprocess_exec``
    does not accept the keyword at all there, and ``adb`` is an async caller.

    ``sys.platform`` is read at CALL time (the same expression as
    ``platform_info.is_windows``): the Windows test fixtures patch it.
    """
    if sys.platform == "win32":
        return {"creationflags": CREATE_NO_WINDOW}
    return {}


@dataclass(frozen=True)
class RunResult:
    """What one run produced. Streams are RAW (never ``None``-coerced)."""

    rc: int | None
    stdout: Any = None
    stderr: Any = None
    timed_out: bool = False
    not_found: bool = False
    #: The original exception object (delegates re-raise it, ``str()`` it, or
    #: render it); ``None`` on a normal finish.
    exc: BaseException | None = field(default=None, compare=False, repr=False)
    #: The raw object ``subprocess.run`` returned (sync runs only).
    completed: object | None = field(default=None, compare=False, repr=False)


def run(
    args: Any,
    *,
    timeout: Any,
    cwd: Any = None,
    env: Any = None,
    input: Any = None,
    text: bool = False,
    no_window: bool = False,
    capture: bool = True,
    shell: bool | None = None,
    rc_coerce: Literal["int", "int_or_zero", "none"] = "int",
) -> RunResult:
    """Run *args* synchronously through ``subprocess.run``; never raises.

    ``check=False`` and ``timeout`` are always forwarded; every other keyword
    only when given (``None`` = not passed; ``input`` is tested ``is not None``
    so ``b""`` is still sent). ``rc_coerce`` is applied inside the guarded
    block: ``"int"`` is ``int(returncode)``, ``"int_or_zero"`` is
    ``int(returncode or 0)``, ``"none"`` leaves it as the object gave it (for a
    caller that returns ``completed`` untouched).
    """
    kwargs: dict[str, Any] = {}
    if capture:
        kwargs["capture_output"] = True
    kwargs["timeout"] = timeout
    kwargs["check"] = False
    if shell is not None:
        kwargs["shell"] = shell
    if cwd is not None:
        kwargs["cwd"] = cwd
    if env is not None:
        kwargs["env"] = env
    if text:
        kwargs["text"] = True
    if input is not None:
        kwargs["input"] = input
    if no_window:
        kwargs.update(no_window_kwargs())
    try:
        proc = subprocess.run(args, **kwargs)  # noqa: S603 - caller's argv
        if rc_coerce == "int_or_zero":
            rc = int(proc.returncode or 0)
        elif rc_coerce == "none":
            rc = proc.returncode
        else:
            rc = int(proc.returncode)
        return RunResult(rc, proc.stdout, proc.stderr, completed=proc)
    except subprocess.TimeoutExpired as exc:
        return RunResult(None, timed_out=True, exc=exc)
    except FileNotFoundError as exc:
        return RunResult(None, not_found=True, exc=exc)
    except Exception as exc:
        return RunResult(None, exc=exc)


async def arun(
    args: Any,
    *,
    timeout: Any,
    stdin: Any,
    communicate_input: bool = False,
    stdin_data: bytes | None = None,
    no_window: bool = False,
    kill_drain: bool = True,
    kill_on_cancel: bool = True,
    ignore_kill_error: bool = False,
    on_drain_error: Callable[[Exception], None] | None = None,
    on_spawned: Callable[[], None] | None = None,
) -> RunResult:
    """Run *args* through ``asyncio.create_subprocess_exec``; see module docs.

    ``stdin`` is always forwarded. ``communicate_input`` picks the legacy
    ``communicate`` call form: ``True`` -> ``communicate(input=stdin_data)``
    (even when ``None``), ``False`` -> ``communicate()``. ``rc`` is the RAW
    ``proc.returncode``; delegates coerce.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=stdin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **(no_window_kwargs() if no_window else {}),
            **_new_session_kwargs(),
        )
    except Exception as exc:
        return RunResult(None, not_found=isinstance(exc, FileNotFoundError), exc=exc)
    if on_spawned is not None:
        on_spawned()
    try:
        pending = _pending(proc, communicate_input, stdin_data)
        stdout, stderr = await asyncio.wait_for(pending, timeout=timeout)
    except asyncio.TimeoutError as exc:
        await _kill(proc, kill_drain, ignore_kill_error, on_drain_error)
        return RunResult(None, timed_out=True, exc=exc)
    except asyncio.CancelledError:
        if kill_on_cancel:
            await _kill(proc, True, ignore_kill_error, None)
        raise
    except Exception as exc:
        # An unexpected failure mid-wait must not leave the child running.
        await _kill(proc, True, True, None)
        return RunResult(None, exc=exc)
    return RunResult(proc.returncode, stdout, stderr)


def _pending(proc: Any, communicate_input: bool, stdin_data: bytes | None):
    """The legacy ``communicate`` call form :func:`arun` awaits."""
    if communicate_input:
        return proc.communicate(input=stdin_data)
    return proc.communicate()


def _new_session_kwargs() -> dict:
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


async def _kill(
    proc: Any,
    drain: bool,
    ignore_kill_error: bool,
    on_drain_error: Callable[[Exception], None] | None,
) -> None:
    """Kill *proc*, then (bounded to 1s) drain it so it is reaped."""
    if ignore_kill_error:
        try:
            _kill_now(proc)
        except Exception:
            pass
    else:
        _kill_now(proc)
    if not drain:
        return
    try:
        await asyncio.wait_for(proc.communicate(), timeout=1)
    except Exception as exc:
        if on_drain_error is not None:
            on_drain_error(exc)
