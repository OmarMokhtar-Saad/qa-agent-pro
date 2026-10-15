"""The probe readings that bracket a dump, so its screen can be trusted past the dump.

A dump takes 2-3 s. :func:`begin` starts a probe reading beside it (the
"before" reading); :func:`trusted_baseline` takes one right after and answers
only when the two are equal, so a change landing while the dump ran is never
mistaken for the screen the dump showed. A carried screen is anchored the same
way (:func:`schedule`), which lets it outlive the 15 s age cap.
"""

from __future__ import annotations

import asyncio

from tools.mobile import dump_latency_state, dump_reuse, screen_probe

ANCHOR_BUDGET_S = 3.0
_ANCHOR: dict = {}
_TASKS: set = set()
_READ: dict = {}
_PRE: dict = {}


def begin(serial: str) -> None:
    """Start the "before" probe beside a dump about to run; never raises."""
    key = str(serial or "")
    try:
        _PRE[key] = asyncio.get_running_loop().create_task(
            screen_probe.read(key, ANCHOR_BUDGET_S)
        )
    except RuntimeError:
        _PRE.pop(key, None)


async def _before(serial: str) -> screen_probe.Reading | None:
    """The "before" reading kept for the latest read, or ``None``."""
    held = _READ.get(str(serial or ""))
    task = held[2] if held else None
    if task is None:
        return None
    try:
        return await asyncio.shield(task)
    except (asyncio.CancelledError, OSError, ValueError):
        return None


async def trusted_baseline(
    serial: str, shown: str, budget: float
) -> screen_probe.Reading | None:
    """A probe reading equal to the "before" one of the dump that showed *shown*.

    ``None`` (dump instead) when *shown* is not the last dump's screen, nothing
    was read before it, the frame is blank (FLAG_SECURE) or the two readings
    differ, i.e. the screen moved while that dump ran.
    """
    key = str(serial or "")
    if not screen_is_current(key, shown):
        return None
    before = await _before(key)
    base = await screen_probe.baseline(key, budget)
    if before is None or base is None or not screen_probe.same(before, base):
        return None
    return base


async def _anchor(serial: str, gen: int) -> None:
    """Probe now; keep the reading only when it equals the "before" one and
    nothing actuated while it ran."""
    before = await _before(serial)
    reading = await screen_probe.baseline(serial, ANCHOR_BUDGET_S)
    if before is None or reading is None or not screen_probe.same(before, reading):
        return
    if gen == dump_reuse.token(serial):
        _ANCHOR[serial] = (gen, reading)


def schedule(serial: str, gen: int) -> None:
    """Take the anchor in the background; never raises, never blocks the dump."""
    serial = str(serial or "")
    _ANCHOR.pop(serial, None)
    try:
        task = asyncio.get_running_loop().create_task(_anchor(serial, gen))
    except RuntimeError:
        return
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


def anchored(serial: str, gen: int) -> bool:
    """True when a probe reading was kept for this generation."""
    held = _ANCHOR.get(str(serial or ""))
    return held is not None and held[0] == gen


async def unmoved(serial: str, gen: int) -> bool:
    """True only when a fresh probe equals the anchor kept for *gen*."""
    held = _ANCHOR.get(str(serial or ""))
    if held is None or held[0] != gen:
        return False
    now = await screen_probe.read(serial, ANCHOR_BUDGET_S)
    return now is not None and screen_probe.same(held[1], now)


def note_read(serial: str, observed: object) -> None:
    """Record the screen hash the latest dump produced, its generation and the
    "before" probe that ran beside that dump."""
    content = observed.get("content") if isinstance(observed, dict) else None
    shown = str((content or {}).get("hash") or "") if isinstance(content, dict) else ""
    key = str(serial or "")
    pre = _PRE.pop(key, None)
    if shown and not observed.get("error"):
        _READ[key] = (shown, dump_reuse.token(key), pre)
    else:
        _READ.pop(key, None)


def screen_is_current(serial: str, shown: str) -> bool:
    """True when *shown* is the last dump's screen and nothing actuated since."""
    key = str(serial or "")
    held = _READ.get(key)
    return (
        bool(shown) and held is not None and held[:2] == (shown, dump_reuse.token(key))
    )


dump_latency_state.BEFORE_DUMP.append(begin)
