"""The state one ``parse`` call keeps, the per-event line view and their small helpers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class _ParseState:
    """Everything one ``parse`` call keeps across events."""

    bindings: list = field(default_factory=list)
    llm: list = field(default_factory=list)
    tools: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    runlog: list = field(default_factory=list)
    recovered: list = field(default_factory=list)
    utterances: list = field(default_factory=list)
    answers: list = field(default_factory=list)
    invocations: list = field(default_factory=list)
    flow_states: list = field(default_factory=list)
    cards: list = field(default_factory=list)
    configs: list = field(default_factory=list)
    open_binding: dict = field(default_factory=dict)
    open_llm: int | None = None
    last_usage: dict | None = None
    turns: dict = field(default_factory=dict)
    runs: dict = field(default_factory=dict)
    clock: dict = field(default_factory=dict)
    # A fresh sentinel per call, so the first event always starts a new run.
    run_index: object = field(default_factory=object)
    # Set once by ``parse`` and never defaulted in practice.
    compiled: dict = field(default_factory=dict)


@dataclass(frozen=True)
class _Line:
    """The fields of one event that every stream reads."""

    ev: dict
    seq: object
    ts: object
    run: str | None
    turn: object
    msg: str
    fields: dict
    cat: object


def _line_of(ev, run_index) -> _Line:
    """The event's fields, with its app run named from ``run_index`` when it has none."""
    run = ev.get("appRunId") or (
        "run-%s" % run_index if run_index is not None else None
    )
    return _Line(
        ev=ev,
        seq=ev.get("seq"),
        ts=ev.get("ts"),
        run=run,
        turn=ev.get("turnId"),
        msg=ev.get("msg") or "",
        fields=ev.get("fields") or {},
        cat=ev.get("category"),
    )


def _bump(state: _ParseState, line: _Line, key: str) -> None:
    """Add one to ``key`` on the turn and the app run of ``line``, when it has them."""
    if line.turn:
        state.turns[line.turn][key] += 1
    if line.run:
        state.runs[line.run][key] += 1


def _take_open(open_calls, name):
    """Oldest still-open request for ``name``, or None."""
    queue = open_calls.get(name)
    return queue.pop(0) if queue else None


def _stamp(line: _Line) -> dict:
    """The three keys every stream entry opens with, in this order."""
    return {"seq": line.seq, "turnId": line.turn, "appRunId": line.run}


def _error_entry(line: _Line, kind: str, detail) -> dict:
    """One error record: the shared ``seq, kind, turnId, appRunId, detail`` shape."""
    return {
        "seq": line.seq,
        "kind": kind,
        "turnId": line.turn,
        "appRunId": line.run,
        "detail": detail,
    }
