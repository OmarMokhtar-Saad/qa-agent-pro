"""The per-step ledger the run manifest keeps beside the timing numbers.

Three bounded pieces, all written by ``step_timing`` while a step is armed:
``marks`` (event counts), ``ledger`` (one row per dump: reason, ms, skipped?)
and ``trace`` (the ordered phase and event names, the replay trace). Pure
helpers over the armed state dict; nothing here awaits or raises.
"""

from __future__ import annotations

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


def on_phase(state: dict, name: str, ms: int) -> None:
    """A timed phase ended: trace it, and ledger it when it was a dump."""
    _push(state.setdefault("trace", []), name, MAX_TRACE_NAMES)
    if name == "ui_dump":
        row = {"reason": "dump", "ms": int(ms), "skipped": False}
        _push(state.setdefault("ledger", []), row, MAX_LEDGER_ROWS)


def on_mark(state: dict, name: str) -> None:
    """An event was counted: trace it, and ledger it when it concerns a dump."""
    _push(state.setdefault("trace", []), name, MAX_TRACE_NAMES)
    if name in _DUMP_EVENTS:
        row = {"reason": name, "ms": 0, "skipped": name in SKIPS}
        _push(state.setdefault("ledger", []), row, MAX_LEDGER_ROWS)


def clean_ledger(value: object) -> list:
    """The stored ledger, kept only where every row is well formed."""
    rows = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            rows.append(
                {
                    "reason": str(item.get("reason") or "")[:40],
                    "ms": max(0, int(item.get("ms") or 0)),
                    "skipped": bool(item.get("skipped")),
                }
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
