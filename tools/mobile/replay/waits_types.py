"""Replay waits: the value types the wait polls and wait steps pass around."""

from __future__ import annotations

from typing import Any, NamedTuple


class Probe(NamedTuple):
    """One watched poll: the ms budget, ``met(previous, current)``, the ticker label.

    *early* lets a wait for something to APPEAR end before its budget once the
    screen is proven quiet (``waits_early.quiet_exit``).
    """

    ms: int
    met: Any
    label: str
    gate: str = ""
    early: bool = False


class Pace(NamedTuple):
    """One poll's timing: the monotonic *deadline* and the *pause* before its read."""

    deadline: float
    pause: float


class Poll(NamedTuple):
    """One gated poll: the probe gate (or ``None``), the deadline, the last read's seconds, the unchanged reads in a row."""

    gate: Any
    deadline: float
    took: float
    quiet: int = 0


class TextWait(NamedTuple):
    """Wait for *text* to appear (or, with *gone*, to leave) within *ms*."""

    text: str
    ms: int
    gone: bool = False


class ElementWait(NamedTuple):
    """Wait for the element *rid* (with *text*) to be present, or gone, within *ms*."""

    rid: str
    text: str
    gone: bool
    ms: int


class WaitSpec(NamedTuple):
    """``(kind, text, ms)``: the one mapping from any wait op onto a condition wait."""

    kind: str
    text: str
    ms: int


class WaitView(NamedTuple):
    """The run's context and the screen a wait polls from."""

    ctx: Any
    screen: Any


class CondStep(NamedTuple):
    """A condition wait step: its action, its spec and its trace entry."""

    action: Any
    spec: Any
    entry: dict


class ChangedStep(NamedTuple):
    """A wait-for-change step: the op, the ms budget and its trace entry."""

    op: str
    wait_ms: int
    entry: dict


class AfterWait(NamedTuple):
    """What a changed wait handed back: the pre-wait screen, changed, reusable."""

    before_wait: Any
    changed: bool
    reusable: bool


class StopView(NamedTuple):
    """The trace entry and the screen a wait stop is built from."""

    entry: dict
    screen: Any
