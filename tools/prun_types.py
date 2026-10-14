"""Types and the no-window helper shared by the process runner leaves (stdlib only)."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, NamedTuple, TypedDict

#: Windows process-creation flag that keeps a child's console hidden. Named here
#: rather than imported from ``subprocess`` because that attribute does not exist
#: on POSIX, so a monkeypatched ``sys.platform`` would make the lookup raise.
CREATE_NO_WINDOW = 0x08000000


class NoWindowKwargs(TypedDict, total=False):
    """What :func:`no_window_kwargs` may return (typed so ``**`` stays checkable)."""

    creationflags: int


class _NewSessionKwargs(TypedDict, total=False):
    start_new_session: bool


def no_window_kwargs() -> NoWindowKwargs:
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


@dataclass(frozen=True)
class RunOpts:
    """Optional keywords of :func:`tools.prun_sync.run` (defaults = the old keywords)."""

    cwd: Any = None
    env: Any = None
    input: Any = None
    text: bool = False
    no_window: bool = False
    capture: bool = True
    shell: bool | None = None
    rc_coerce: Literal["int", "int_or_zero", "none"] = "int"


@dataclass(frozen=True)
class ArunOpts:
    """Keywords of :func:`tools.prun_async.arun` (``stdin`` is required, as before)."""

    stdin: Any
    communicate_input: bool = False
    stdin_data: bytes | None = None
    no_window: bool = False
    kill_drain: bool = True
    kill_on_cancel: bool = True
    ignore_kill_error: bool = False
    on_drain_error: Callable[[Exception], None] | None = None
    on_spawned: Callable[[], None] | None = None
    quiet: bool = False


class KillPlan(NamedTuple):
    """How :func:`tools.prun_async._kill` ends a child."""

    drain: bool
    ignore_error: bool
    on_drain_error: Callable[[Exception], None] | None
