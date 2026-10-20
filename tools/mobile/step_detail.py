"""Per-command adb cost for a step's ledger, the part ``step_fold`` hides.

A step folds dozens of adb calls into one ``adb n=27`` row. This keeps a bounded
``{label: (n, seconds)}`` table in the armed step state and renders its costliest
labels as ``adb_cmd`` rows (label in ``caller``), so the ledger says WHICH calls
took the time. A label is the command's first words, never an argument value.

Each row is a BUCKET of calls sharing a label, not one call: ``ms`` is the
bucket total, ``n`` its call count, and the caller text carries the average per
call, so no reader takes "5 rows of 3.3 s" for five 3.3 s commands.
"""

from __future__ import annotations

import time

#: Distinct command labels kept per step; a later new label is counted as ``other``.
MAX_LABELS = 24

#: Costliest labels written into the ledger.
TOP_LABELS = 5

#: Words of an adb command kept in its label.
LABEL_WORDS = 3

OTHER = "other"
REASON = "adb_cmd"


def label_of(body: str) -> str:
    """The first words of an adb command body (``shell input tap``)."""
    words = str(body or "").split()
    return " ".join(words[:LABEL_WORDS])[:40] or OTHER


def bucket_caller(label: str, count: int, total: float) -> str:
    """``label: Nx avg M ms`` clipped to the ledger's 40-char caller field."""
    tail = f": {count}x avg {int(total * 1000 / max(1, count))}ms"
    return label[: max(0, 40 - len(tail))] + tail


def note_adb(state: dict | None, body: str, seconds: float) -> None:
    """Add one adb call of *seconds* to the armed *state*'s per-command table."""
    if state is None:
        return
    table = state.setdefault("adb_cmds", {})
    label = label_of(body)
    if label not in table and len(table) >= MAX_LABELS:
        label = OTHER
    count, total = table.get(label, (0, 0.0))
    table[label] = (count + 1, total + _exclusive(state, seconds))


def _exclusive(state: dict, seconds: float) -> float:
    """The part of this call's *seconds* no other adb call already covers.

    Calls overlap (a dump runs beside probes), so summing their wall time
    reported 8.9 s of adb inside a 3.1 s step. Calls are noted as they END, in
    order, so the union is one moving edge: only the time past it is new.
    """
    end = time.monotonic()
    new = max(0.0, end - max(end - seconds, float(state.get("adb_reach", 0.0))))
    state["adb_reach"] = max(end, float(state.get("adb_reach", 0.0)))
    return min(new, max(0.0, seconds))


def rows(state: dict) -> list:
    """The costliest labels as ledger rows, biggest first."""
    table = state.get("adb_cmds") or {}
    top = sorted(table.items(), key=lambda kv: -kv[1][1])[:TOP_LABELS]
    return [
        {
            "reason": REASON,
            "caller": bucket_caller(label, count, total),
            "ms": int(total * 1000),
            "n": count,
            "skipped": False,
            "nested": True,
        }
        for label, (count, total) in top
    ]
