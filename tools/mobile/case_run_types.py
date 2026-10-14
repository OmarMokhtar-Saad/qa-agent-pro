"""NamedTuples of the case runner."""

from __future__ import annotations

from typing import NamedTuple

from tools.mobile.replay.context import Context


class _Settle(NamedTuple):
    """What the foreground wait saw: the last dump, its size and density."""

    error: dict | None
    dumped: dict
    sized: dict
    dpi: dict
    screen: dict
    in_front: bool


class _Step(NamedTuple):
    """What one replay left behind: the facts every verdict branch reads."""

    run_id: str
    tc_id: str
    result: dict
    trace: list
    used: int
    new_screen: dict


class _Ref(NamedTuple):
    """The case a piece of evidence work is for: the run, the case, the device."""

    run_id: str
    tc_id: str
    ctx: Context


class _Case(NamedTuple):
    """The checkpoint's addressing: run, case and the caller's case view."""

    run_id: str
    tc_id: str
    view: dict


class _CpFields(NamedTuple):
    """The keyword fields of `_checkpoint`: the same set and defaults as before."""

    verdict: str
    status: str
    reason: str
    trace: list
    escapes: int
    packet: object
    uncharged: object = None
    guard_stop: object = None
    queued_actions: object = None


class _Opened(NamedTuple):
    """A case that has been cleanly launched: its id, view and open evidence."""

    tc_id: str
    view: dict
    evidence: dict


class _Plan(NamedTuple):
    """What the planning checkpoint is built from, besides the opened case."""

    used: int
    started: float
    packet: dict


class _Ask(NamedTuple):
    """What an escape stop carries to its payload: the charge and why."""

    used: int
    uncharged: dict
    reason_key: str


class Submission(NamedTuple):
    """One planner script handed to ``submit_case``."""

    run_id: str
    case: object
    ctx: Context
    raw_script: object
    screen: dict | None = None


class _Intake(NamedTuple):
    """What a submission came to before any replay: a ready answer or a script."""

    early: dict | None
    case: _Case
    script: object
