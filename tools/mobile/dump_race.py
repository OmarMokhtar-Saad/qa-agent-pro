"""Race the a11y service against the uiautomator fallback after a ``no_window`` expiry.

``no_window`` (:mod:`tools.mobile.a11y_retry`) is the service saying no window has a
readable root: a screen transition, or a window another owner holds (a system
installer dialog, audit-1148 E04). After the short re-ask budget the dump used to fall
back to uiautomator ALONE, a 3.3 s read on a loaded device, even when the service
recovered 300 ms in. Now the fallback starts and the service keeps being asked beside
it: whichever answers first serves the dump, and the loser is cancelled (an adb
command that is cancelled is killed). No sleep: each ask is itself a round trip.

A fallback that wins marks the serial *blind*, so the dump after it does not spend
the re-ask budget again (:func:`blind`): one ask, then the race. An a11y tree that
wins clears it.

The winner is handed to the caller's *accept* (the normal path's cap/``<`` check, source
tag and streak reset), so a raced tree is served exactly like an unraced one.

Known gap (follow-up): cancelling the fallback kills the host adb client only; the
device-side ``uiautomator dump`` may keep running and write the same remote path.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from tools.mobile import step_timing
from tools.mobile.dump_latency_state import MAX_SERIALS

Ask = Callable[[str], Awaitable[dict]]
Fallback = Callable[[], Awaitable[dict]]
Accept = Callable[[dict], Optional[str]]

#: serial -> the raw a11y ask of a dump whose re-ask budget just expired (spent once).
_PENDING: dict[str, Ask] = {}
#: Serials whose last race was won by the fallback (a dict for oldest-first eviction).
_BLIND: dict[str, bool] = {}


def _remember(table: dict, serial: str, value) -> None:
    """Set ``table[serial]``, dropping the oldest serials past MAX_SERIALS."""
    table.pop(serial, None)
    table[serial] = value
    while len(table) > MAX_SERIALS:
        table.pop(next(iter(table)))


def arm(serial: str, ask: Ask) -> None:
    """Remember *ask* for the fallback read that follows this expired dump."""
    _remember(_PENDING, str(serial), ask)


def disarm(serial: str) -> None:
    """A tree arrived (or the provider went): nothing to race, the window is readable."""
    _PENDING.pop(str(serial), None)
    _BLIND.pop(str(serial), None)


def blind(serial: str) -> bool:
    """True when the previous race was lost by the service (skip the re-ask budget)."""
    return str(serial) in _BLIND


def _tree(got: dict) -> bool:
    return not got.get("error") and bool(got.get("content"))


async def _asks(ask: Ask, serial: str) -> dict:
    """Ask until a tree arrives; any refusal but ``no_window`` ends the asking."""
    while True:
        got = await ask(serial)
        await asyncio.sleep(0)  # a yield, not a wait: the fallback must get its turn
        if _tree(got) or "no_window" not in str(got.get("error") or ""):
            return got


async def _cancel(task: asyncio.Future) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@dataclass
class _Race:
    serial: str
    ask: Ask
    fallback: Fallback
    accept: Accept

    def _won(self, fast: asyncio.Future) -> Optional[dict]:
        if not (fast.done() and _tree(fast.result())):
            return None
        xml = self.accept(fast.result())
        return None if xml is None else {"error": None, "content": xml}

    async def run(self) -> dict:
        slow = asyncio.ensure_future(self.fallback())
        fast = asyncio.ensure_future(_asks(self.ask, self.serial))
        try:
            await asyncio.wait({slow, fast}, return_when=asyncio.FIRST_COMPLETED)
            won = self._won(fast)
            if won is not None:
                step_timing.note("a11y_race_won")
                _BLIND.pop(self.serial, None)
                return won
            if not fast.done():  # the fallback beat a still-refusing service
                _remember(_BLIND, self.serial, True)
            return await slow
        finally:
            await _cancel(slow)
            await _cancel(fast)


async def first(
    serial: str, fallback: Fallback, accept: Optional[Accept] = None
) -> dict:
    """*fallback*'s read, or the a11y tree that beat it when a race is armed.

    Without *accept* (a ``use_provider=False`` read) the armed ask is dropped, unraced.
    """
    ask = _PENDING.pop(str(serial), None)
    if ask is None or accept is None:
        return await fallback()
    return await _Race(str(serial), ask, fallback, accept).run()
