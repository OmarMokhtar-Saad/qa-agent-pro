"""The per-step ledger the run manifest keeps beside the timing numbers.

Three bounded pieces, all written by ``step_timing`` while a step is armed:
``marks`` (event counts), ``ledger`` (one row per dump: reason, ms, skipped?,
and the optional ``caller`` site that asked) and ``trace`` (the ordered phase
and event names, the replay trace). Pure helpers over the armed state dict;
nothing here awaits or raises.
"""

from __future__ import annotations

from tools.mobile import step_fold, step_site

#: Distinct mark names kept per step.
MAX_MARKS = 20

#: Dump rows kept per step; the newest win.
MAX_LEDGER_ROWS = 40

#: Trace names kept per step; the newest win.
MAX_TRACE_NAMES = 60

#: Marks that mean a dump was NOT taken (reused, carried, skipped, cut).
SKIPS = frozenset(
    {
        "dump_skipped",
        "dump_reused",
        "dump_reused_raw",
        "dump_carried",
        "dump_cut_served",
        "probe_skip",
        "gate_skip",
    }
)

_DUMP_EVENTS = SKIPS | {"dump_cut"}


def _push(items: list, value: object, cap: int) -> None:
    items.append(value)
    del items[:-cap]


def _push_row(state: dict, row: dict) -> None:
    """Add a ledger row; past the cap the oldest fold into per-reason sums."""
    rows = state.setdefault("ledger", [])
    rows.append(row)
    if len(rows) >= MAX_LEDGER_ROWS:  # leave room for step_cover's closing row
        state["ledger"] = step_fold.fold(rows, MAX_LEDGER_ROWS - 1)


def _with_caller(row: dict) -> dict:
    """*row* plus the optional ``caller`` key naming the asking code site."""
    site = step_site.caller()
    return {**row, "caller": site} if site else row


def on_phase(state: dict, name: str, ms: int) -> None:
    """A timed phase ended: trace it and ledger it (a dump is reason ``dump``)."""
    _push(state.setdefault("trace", []), name, MAX_TRACE_NAMES)
    reason = "dump" if name == "ui_dump" else name
    row = _with_caller({"reason": reason, "ms": int(ms), "skipped": False})
    if name == "ui_dump" and state.get("source"):
        row["source"] = str(state["source"])[:40]
    _push_row(state, row)


def on_mark(state: dict, name: str) -> None:
    """An event was counted: trace it, and ledger it when it concerns a dump."""
    _push(state.setdefault("trace", []), name, MAX_TRACE_NAMES)
    if name in _DUMP_EVENTS:
        row = _with_caller({"reason": name, "ms": 0, "skipped": name in SKIPS})
        _push_row(state, row)


def on_note(state: dict, reason: str, ms: int) -> None:
    """A non-dump fact (probe cost, why a probe skip did not fire): ledger it."""
    _push(state.setdefault("trace", []), reason, MAX_TRACE_NAMES)
    row = _with_caller({"reason": reason, "ms": int(ms), "skipped": False})
    _push_row(state, row)


def _clean_row(item: dict) -> dict:
    row = {
        "reason": str(item.get("reason") or "")[:40],
        "ms": max(0, int(item.get("ms") or 0)),
        "skipped": bool(item.get("skipped")),
    }
    if item.get("caller"):
        row["caller"] = str(item["caller"])[:40]
    if item.get("source"):
        row["source"] = str(item["source"])[:40]
    if int(item.get("n") or 0) > 1:
        row["n"] = int(item["n"])
    return row


def clean_ledger(value: object) -> list:
    """The stored ledger, kept only where every row is well formed."""
    rows = (
        [_clean_row(i) for i in value if isinstance(i, dict)]
        if isinstance(value, list)
        else []
    )
    return rows[-MAX_LEDGER_ROWS:]


def clean_trace(value: object) -> list:
    """The stored trace as short strings."""
    names = [str(n)[:40] for n in value] if isinstance(value, list) else []
    return names[-MAX_TRACE_NAMES:]


def clean_marks(value: object) -> dict:
    """Stored marks as ``{name: count}``, at most :data:`MAX_MARKS` names."""
    items = list(value.items()) if isinstance(value, dict) else []
    return {str(k)[:40]: max(0, int(v)) for k, v in items[:MAX_MARKS]}
