"""A bounded re-ask when the a11y service says ``no_window`` for one dump.

``no_window`` is the CONNECTED service saying no window has a readable root right now:
the few hundred ms around a screen transition or a dialog opening. Falling back to
uiautomator costs seconds on a loaded device, so the provider asks the service again,
back to back, until a tree arrives or the budget is spent. Each ask is itself a
round trip, so there is no sleep constant. The budget is the smaller of
:data:`RETRY_CAP_MS` and this serial's uiautomator p90 (re-asking is never worth
more than the fallback it avoids). A tree that arrives is newer than the
actuation by construction, because it was asked after it. Only ``no_window`` is
retried; every other refusal is returned untouched.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from tools.mobile import adb_dump, dump_latency_state, dump_race, step_timing

#: The longest a dump re-asks, in ms.
RETRY_CAP_MS = 400
#: The refusal code that is retried.
NO_WINDOW = "no_window"
#: The uiautomator buckets the budget reads, first one with samples wins.
FALLBACK_SOURCES = (adb_dump.SOURCE_PROVIDER_FAILED, adb_dump.SOURCE_NO_PROVIDER)

Ask = Callable[[str], Awaitable[dict]]


@dataclass
class Budget:
    """How long to re-ask, on an injectable monotonic clock."""

    ms: int
    now: Callable[[], float] = field(default=time.monotonic)

    def deadline(self) -> float:
        return self.now() + self.ms / 1000.0


def budget_ms(serial: str) -> int:
    """:data:`RETRY_CAP_MS`, or the uiautomator p90 of *serial* when that is smaller.

    0 while the last race was won by the fallback (:mod:`tools.mobile.dump_race`): the
    window is held by another owner, so the re-ask would only delay the race.
    """
    if dump_race.blind(serial):
        return 0
    for source in FALLBACK_SOURCES:
        p90 = dump_latency_state.p90_ms(serial, source)
        if p90 is not None:
            return max(0, min(RETRY_CAP_MS, p90))
    return RETRY_CAP_MS


def _refused(got: dict) -> bool:
    return NO_WINDOW in str(got.get("error") or "")


def _noted(name: str, asks: int, waited_s: float) -> None:
    step_timing.note(name + ":" + str(asks) + ":" + str(int(waited_s * 1000)))


async def until_tree(ask: Ask, serial: str, budget: Budget | None = None) -> dict:
    """*ask*'s first answer, re-asked while it is ``no_window`` and the budget lasts."""
    got = await ask(serial)
    if not _refused(got):
        dump_race.disarm(serial)
        return got
    limit = budget or Budget(budget_ms(serial))
    started, end, asks = limit.now(), limit.deadline(), 0
    while _refused(got) and limit.now() < end:
        got = await ask(serial)
        asks += 1
    if asks:
        name = "a11y_retry_expired" if _refused(got) else "a11y_retry"
        _noted(name, asks, limit.now() - started)
    if _refused(got):
        dump_race.arm(serial, ask)
    else:
        dump_race.disarm(serial)
    return got
