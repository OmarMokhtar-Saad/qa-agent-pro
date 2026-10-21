"""A server-issued scroll/swipe is an EXPECTED change of the screen (fix/1580 C04).

After the server swipes, :func:`after` waits (bounded, event-driven) for the a11y
service's next event generation and remembers the swipe. The next a11y read that
repeats the previous tree is then a real "unchanged" (end of a list, a short
scroll): :func:`take` tells :mod:`tools.mobile.a11y_fresh` to serve it, with no
stale refusal and no strike. The mark is used up by that one read.
"""

from __future__ import annotations

import time

from tools.mobile import a11y_next, dump_reuse

#: Longest wait (s) for the event our own swipe causes.
EVENT_WAIT_S = 1.0

#: serial -> the actuation token right after our swipe; any later tap changes it.
_EXPECTED: dict[str, int] = {}


async def after(serial: str, held: int | None) -> None:
    """Wait (bounded) for *serial*'s event after our swipe, then mark the next read."""
    if held is not None:
        await a11y_next.next_generation(serial, held, time.monotonic() + EVENT_WAIT_S)
    # Taken after our own commands (the swipe and the wait's dumps), before any tap.
    _EXPECTED[str(serial)] = dump_reuse.token(serial)


def take(serial: str, token_now: int) -> bool:
    """True once after a swipe, only while no tap/key ran since (token unchanged)."""
    return _EXPECTED.pop(str(serial), None) == token_now


def forget(serial: str) -> None:
    """Drop the mark (a fresh enable, a restore)."""
    _EXPECTED.pop(str(serial), None)
