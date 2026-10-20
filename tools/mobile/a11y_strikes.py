"""Stale verdicts per serial: they accumulate, and healthy reads pay them back."""

from __future__ import annotations

#: Stale verdicts in one run before fast reading is benched. One is a transition
#: lag; two can be two slow screens; a third means the service is persistently wrong.
MAX_STALE_STRIKES = 3

#: serial -> stale verdicts not yet paid back.
STRIKES: dict[str, int] = {}


def add(serial: str) -> int:
    """Count one stale verdict; the new total."""
    STRIKES[serial] = STRIKES.get(serial, 0) + 1
    return STRIKES[serial]


def decay(serial: str) -> None:
    """A confirmed-good read pays one strike back."""
    if STRIKES.get(serial, 0) > 1:
        STRIKES[serial] -= 1
    else:
        STRIKES.pop(serial, None)


def reset(serial: str, left: int = 0) -> None:
    """Forget the strikes, or keep ``left`` (a re-armed reader gets one chance)."""
    if left > 0:
        STRIKES[serial] = left
    else:
        STRIKES.pop(serial, None)
