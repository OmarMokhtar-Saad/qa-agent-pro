"""Per-command adb cost for a step's ledger, the part ``step_fold`` hides.

A step folds dozens of adb calls into one ``adb n=27`` row. This keeps a bounded
``{label: (n, seconds)}`` table in the armed step state and renders its costliest
labels as ``adb_cmd`` rows (label in ``caller``), so the ledger says WHICH calls
took the time. A label is the command's first words, never an argument value.
"""

from __future__ import annotations

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


def note_adb(state: dict | None, body: str, seconds: float) -> None:
    """Add one adb call of *seconds* to the armed *state*'s per-command table."""
    if state is None:
        return
    table = state.setdefault("adb_cmds", {})
    label = label_of(body)
    if label not in table and len(table) >= MAX_LABELS:
        label = OTHER
    count, total = table.get(label, (0, 0.0))
    table[label] = (count + 1, total + seconds)


def rows(state: dict) -> list:
    """The costliest labels as ledger rows, biggest first."""
    table = state.get("adb_cmds") or {}
    top = sorted(table.items(), key=lambda kv: -kv[1][1])[:TOP_LABELS]
    return [
        {
            "reason": REASON,
            "caller": label,
            "ms": int(total * 1000),
            "n": count,
            "skipped": False,
        }
        for label, (count, total) in top
    ]
