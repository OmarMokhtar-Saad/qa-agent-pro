"""Mobile session submit: parameter objects (split from session_submit)."""

from __future__ import annotations

from dataclasses import dataclass

from tools.mobile.replay.context import Context


@dataclass(frozen=True)
class SubmitCall:
    """One ``submit``: which answer of which run, and the tester's inputs."""

    run_id: str
    tc_id: str
    script: object
    session_token: str = ""
    tester_input: str = ""
    tester_input_field: str = ""
    tester_inputs: dict | None = None
    confirm_destructive: bool = False
    budget: object = None
    route_expect: object = None


@dataclass(frozen=True)
class ExploreCall:
    """One exploratory submit: the raw reply, its context and the resolved run."""

    run_id: str
    raw: object
    ctx: Context
    resolved: dict


@dataclass(frozen=True)
class _SuiteTarget:
    """Which case of which run a suite-lane submit answers, and its reply context."""

    run_id: str
    tc_id: str
    body: dict
    field: str


@dataclass(frozen=True)
class _ReplayedTurn:
    """What a replayed exploratory turn hands to its commit step."""

    outcome: dict
    clip: object
    clip_tc_id: str


@dataclass(frozen=True)
class _TurnReply:
    """What the chat reply of one folded exploratory turn is built from."""

    case: dict
    notice: str
    resolved: dict
    stopped: bool


@dataclass(frozen=True)
class _Settle:
    """What persisting and answering one committed exploratory turn needs."""

    run_id: str
    resolved: dict
    turn: dict
    outcome: dict
    checkpoint: dict
