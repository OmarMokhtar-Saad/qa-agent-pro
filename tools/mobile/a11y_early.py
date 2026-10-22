"""Before a repeated a11y tree is called stale: is it just an early read?

After a cross-app tap the a11y service may not have heard the new window yet, so
the tree repeats. Judging that as stale cost a strike plus a slow uiautomator dump
(measured 11 s). So, before :mod:`tools.mobile.a11y_fresh` asks uiautomator:

1. one cheap focus read: when the focused app is not the tree's app, it is an early
   read (no strike);
2. a wait for the next event generation (bounded, no sleep) and a re-read: a tree
   that moved is served as is;
3. ``moved`` tells the caller whether the service heard events meanwhile: only then
   may a uiautomator disagreement count as a strike.

A service without an event clock cannot prove it silent, so it counts as moved.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

from tools.mobile import a11y_events, a11y_next, adb_facts, dump_reuse, step_timing
from tools.mobile.a11y_pkgs import is_ime
from tools.mobile.xml_attrs import attr_pattern, attr_values

#: Longest wait (s) for the screen's next event before judging a repeat.
WAIT_S = 1.0
#: Device ms with no event after which the screen has rested.
EVENT_QUIET_MS = 200
#: Longest uiautomator confirm (s); a slower one is "unchecked", never a verdict.
CONFIRM_CAP_S = 3.0
#: Longest focus read (s).
FOCUS_S = 2.0
EARLY_NOTE = "a11y_early_read"
EARLY_REFUSAL = "the a11y tree is older than the focused window"
REREAD_REFUSAL = "the a11y tree moved while waiting for the screen to settle"

Provider = Callable[[str], Awaitable[dict]]


def packages(xml: str) -> frozenset:
    """The ``package`` attribute values of *xml*: which apps own the tree."""
    return frozenset(attr_values(attr_pattern("package"), xml))


_FOCUS: dict[str, tuple] = {}


def _focus_stamp(serial: str) -> tuple | None:
    """(armed step, actuation token, a11y generation); None when not cacheable.

    No armed step, or no event clock: the app may navigate on its own, so a
    cached focus could go stale and is never kept.
    """
    step = step_timing.armed_step()
    generation = a11y_next.generation_of(serial)
    if step is None or generation is None:
        return None
    return (step, dump_reuse.token(serial), generation)


async def focused_package(serial: str) -> str:
    """The focused window's package, or ``""`` when it cannot be read in time."""
    stamp = _focus_stamp(serial)
    held = _FOCUS.get(serial)
    if stamp is not None and held is not None and held[0] == stamp:
        return held[1]
    try:
        got = await asyncio.wait_for(
            adb_facts.current_activity(serial, timeout=int(FOCUS_S)), FOCUS_S
        )
    except (TimeoutError, OSError, ValueError, KeyError):
        return ""
    value = str(got.get("content") or "").split("/", 1)[0]
    if stamp is not None and value:
        _FOCUS[serial] = (stamp, value)
    return value


async def elsewhere(serial: str, packages: frozenset) -> bool:
    """True when the focused app is none of the tree's (non-keyboard) apps."""
    mine = {p for p in packages if not is_ime(p)}
    focus = await focused_package(serial)
    return bool(mine and focus and not is_ime(focus) and focus not in mine)


async def _rest(serial: str, deadline: float) -> None:
    """Block on events until the screen has been quiet for EVENT_QUIET_MS."""
    while (clock := a11y_events.latest(serial)) is not None:
        left = (EVENT_QUIET_MS - clock.quiet_ms()) / 1000
        if left <= 0 or clock.generation is None:
            return
        until = min(deadline, time.monotonic() + left)
        if not await a11y_next.next_generation(serial, clock.generation, until):
            return


async def news(serial: str) -> bool:
    """True when the next event generation arrived (then settled) within WAIT_S."""
    held = a11y_next.generation_of(serial)
    if held is None:
        return True
    deadline = time.monotonic() + WAIT_S
    if not await a11y_next.next_generation(serial, held, deadline):
        return False
    await _rest(serial, deadline)
    return True


def refusal(reason: str, **extra: object) -> dict:
    """A refusal envelope that carries no error text of the screen."""
    return {"error": reason, "content": None, **extra}


async def reread(
    serial: str, inner: Provider | None, same: Callable[[str], bool]
) -> dict | None:
    """*inner*'s new dump when its tree is not the *same* one, else ``None``."""
    if inner is None:
        return None
    got = await inner(serial)
    xml = str(got.get("content") or "")
    return None if got.get("error") or not xml or same(xml) else got


async def settle(
    serial: str, packages: frozenset, redo: Callable
) -> tuple[dict | None, bool]:
    """``(answer, moved)``: an early/reread refusal, or ``None`` to go on judging.

    *redo* returns the re-read dump when the tree moved, else ``None``.
    """
    away = await elsewhere(serial, packages)
    moved = await news(serial)
    got = await redo() if moved else None
    if got is not None:
        return refusal(REREAD_REFUSAL, reread=got), moved
    if away:
        step_timing.note(EARLY_NOTE)
        return refusal(EARLY_REFUSAL), moved
    return None, moved
