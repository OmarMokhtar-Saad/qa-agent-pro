"""The probe readings that bracket a dump, so its screen can be trusted past the dump.

A dump takes 2-3 s. :func:`begin` starts a probe reading beside it (the
"before" reading); :func:`trusted_baseline` takes one right after and answers
only when the two are equal, so a change landing while the dump ran is never
mistaken for the screen the dump showed. A carried screen is anchored the same
way (:func:`schedule`), outliving the 15 s age cap.
"""

from __future__ import annotations

import asyncio
import time

from tools.mobile import (
    dump_latency_state,
    dump_reuse,
    screen_probe,
    screen_reuse,
    step_timing,
)

ANCHOR_BUDGET_S = 3.0
#: Seconds one anchor probe is assumed to cost until one has been timed.
PROBE_GUESS_S = 1.0
#: The dump must cost this many times the anchor probe before waiting pays back.
WAIT_PAYBACK_RATIO = 2.0
_COST: dict = {}
_ANCHOR: dict = {}
_TASKS: set = set()
_READ: dict = {}
_PRE: dict = {}
_PENDING: dict = {}


def begin(serial: str) -> None:
    """Start the "before" probe beside a dump about to run; never raises."""
    key = str(serial or "")
    screen_reuse.drop(key)
    if cheaper_to_dump(key):  # note_read already popped the last one
        return
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
    base = screen_reuse.fresh(key) or await screen_probe.baseline(key, budget)
    if before is None or base is None or not screen_probe.same(before, base):
        return None
    return base


def before_action(serial: str, shown: str) -> screen_probe.Reading | None:
    """The trusted probe reading kept for the dump that showed *shown*, even
    after an action ran (an action bumps the generation, never the reading).

    ``None`` unless the anchor and the last dump are the same generation and
    that dump showed *shown*: a later dump or an untaken anchor is distrusted.
    """
    key = str(serial or "")
    held, read = _ANCHOR.get(key), _READ.get(key)
    if not shown or held is None or read is None:
        return None
    return held[1] if (read[0], read[1]) == (shown, held[0]) else None


async def _anchor(serial: str, gen: int) -> None:
    """Probe now; keep the reading only when it equals the "before" one and
    nothing actuated while it ran."""
    before = await _before(serial)
    started = time.monotonic()
    reading = await screen_probe.baseline(serial, ANCHOR_BUDGET_S)
    _COST[serial] = time.monotonic() - started
    step_timing.note("probe", int(_COST[serial] * 1000))
    screen_reuse.keep(serial, reading)
    if before is None or reading is None or not screen_probe.same(before, reading):
        return
    if gen == dump_reuse.token(serial):
        _ANCHOR[serial] = (gen, reading)


def schedule(serial: str, gen: int) -> None:
    """Take the anchor in the background; never raises, never blocks the dump."""
    serial = str(serial or "")
    _ANCHOR.pop(serial, None)
    dump_latency_state.note_p90(serial)
    if cheaper_to_dump(serial):
        return
    try:
        task = asyncio.get_running_loop().create_task(_anchor(serial, gen))
    except RuntimeError:
        return
    _TASKS.add(task)
    _PENDING[serial] = task
    task.add_done_callback(_TASKS.discard)


def worth_waiting(serial: str) -> bool:
    """True when this device's dumps cost clearly more than its anchor probe."""
    key = str(serial or "")
    dump_ms = dump_latency_state.next_dump_p90(key)
    cost = _COST.get(key, PROBE_GUESS_S)
    return dump_ms is not None and dump_ms / 1000.0 > WAIT_PAYBACK_RATIO * cost


def cheaper_to_dump(serial: str) -> bool:
    """True once dump AND probe are timed and the dump is too fast to pay for it."""
    key = str(serial or "")
    timed = key in _COST and dump_latency_state.next_dump_p90(key) is not None
    return timed and not worth_waiting(key)


def miss_reason(serial: str) -> str:
    """Why :func:`before_action` holds no baseline (a ledger reason)."""
    key = str(serial or "")
    if key in _ANCHOR:
        return "other_screen"
    if cheaper_to_dump(key):
        return "cheaper_to_dump"
    task = _PENDING.get(key)
    if task is not None and not task.done() and not worth_waiting(key):
        return "cheaper_to_dump"
    return "no_anchor" if key in _READ else "no_baseline"


async def ready(serial: str) -> None:
    """Let the anchor probe still running finish before an action; never raises."""
    task = _PENDING.get(str(serial or ""))
    if task is None or task.done() or not worth_waiting(serial):
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), 2 * ANCHOR_BUDGET_S)
    except (TimeoutError, OSError, ValueError, RuntimeError):
        return


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
    """Record the latest dump's screen hash, generation and "before" probe."""
    content = observed.get("content") if isinstance(observed, dict) else None
    shown = str((content or {}).get("hash") or "") if isinstance(content, dict) else ""
    key = str(serial or "")
    pre = _PRE.pop(key, None)
    if shown and isinstance(observed, dict) and not observed.get("error"):
        _READ[key] = (shown, dump_reuse.token(key), pre)
    else:
        _READ.pop(key, None)


def screen_is_current(serial: str, shown: str) -> bool:
    """True when *shown* is the last dump's screen and nothing actuated since."""
    key = str(serial or "")
    held = _READ.get(key) or ()
    return bool(shown) and held[:2] == (shown, dump_reuse.token(key))


dump_latency_state.BEFORE_DUMP.append(begin)
