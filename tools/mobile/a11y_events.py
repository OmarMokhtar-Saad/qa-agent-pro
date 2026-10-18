"""The qa-ime accessibility event clock, per serial (qa-ime 1.2.1+).

A fast a11y dump reply may carry one ``n:LAST,NOW,COUNT[,GEN]`` part: the device
uptime (ms) of the service's last window/content event, the uptime when the
dump was taken, and how many such events the service has seen since it
connected. The reply parser records it here; waits read it. A reply without
the part (qa-ime 1.2.0 and older) records nothing, so ``latest`` stays
``None`` and every wait keeps its old path.
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class EventClock:
    last_ms: int
    now_ms: int
    count: int
    generation: int | None = None  # every-event counter (qa-ime 1.2.2+)

    def quiet_ms(self) -> int:
        """How long the screen had sent no event when this dump was taken."""
        return max(0, self.now_ms - self.last_ms)


_LATEST: dict = {}


def parse(text: str) -> EventClock | None:
    """``"LAST,NOW,COUNT"`` -> an EventClock, or ``None`` when malformed."""
    bits = str(text or "").split(",")
    if len(bits) not in (3, 4):
        return None
    try:
        last, now, count, *gen = (int(b) for b in bits)
    except (ValueError, OverflowError):
        return None
    if min(last, now, count, *gen) < 0:
        return None
    return EventClock(last, now, count, gen[0] if gen else None)


def record(serial: str, clock: EventClock | None) -> None:
    """Remember the clock of *serial*'s latest dump; ``None`` forgets it."""
    if clock is None:
        _LATEST.pop(str(serial or ""), None)
        return
    _LATEST[str(serial or "")] = clock


def latest(serial: str) -> EventClock | None:
    """The clock of *serial*'s latest fast dump, or ``None`` (capability off)."""
    return _LATEST.get(str(serial or ""))
