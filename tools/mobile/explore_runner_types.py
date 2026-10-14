"""Parameter objects of the exploratory lane (frozen NamedTuples, no behaviour).

Cut from ``tools.mobile.explore_runner`` so the lane's functions stay within three parameters.
"""

from __future__ import annotations

from typing import Any, NamedTuple


class StateSpec(NamedTuple):
    """What ``new_state`` needs beyond the goal."""

    watch_for: Any = ()
    guard: bool = True
    charter: object = None
    now: float | None = None


class TurnEnv(NamedTuple):
    """The device context and injected clock of one ``next_turn`` call."""

    ctx: object
    now: float | None = None


class TurnParts(NamedTuple):
    """The screen, packet and remaining budget of a running turn."""

    screen: dict | None
    packet: object
    remaining: dict


class Held(NamedTuple):
    """A state body and the screen observed for it."""

    body: dict
    screen: dict


class Budget(NamedTuple):
    """A turns budget and a deadline."""

    turns: int
    deadline: float


class Recorded(NamedTuple):
    """A reply's finding and the nudge shown when it had none."""

    finding: str
    notice: str
