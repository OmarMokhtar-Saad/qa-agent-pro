"""Replay engine: the actions a stopped script did not run (fix/1460 W2).

Every stop except the budget stop names them on the reply as ``not_run``.
They are NOT ``queued_actions``: a queue is replayed first on the next submit
(``case_run_escape``, ``session_submit_commit``), and a script that stopped
because the screen was not what it expected must never resume by itself.
"""

from __future__ import annotations

from typing import Any

from tools.mobile.replay.context import _ReplayRun
from tools.mobile.replay.verdicts import _serialize_queued

#: Trace flag: the step's target wait was interrupted before the step actuated.
WAITED_NO_ACTION = "target_wait_stop"

# Outcomes of a step that was refused BEFORE it actuated anything.
_NOT_ACTUATED = frozenset(
    {
        "missing_element",
        "tap_text_ambiguous",
        "route_mismatch",
        "knowledge_avoid",
        "guard_stop",
        "screen_mismatch",
        "refused",
        "device_error",
    }
)


def _stopped_entry(run: _ReplayRun, index: int) -> dict | None:
    """The trace entry of the step the script stopped at, if it reached the trace."""
    for entry in reversed(run.trace):
        if entry.get("index") == index:
            return entry
    return None


def _ran(entry: dict | None) -> bool:
    """Whether the stopped step itself actuated something."""
    if entry is None:
        return False
    outcome = str(entry.get("outcome") or "")
    if outcome == "interrupted":
        op = (entry.get("action") or {}).get("op")
        return op != "tap_text_if_present" and not entry.get(WAITED_NO_ACTION)
    return outcome not in _NOT_ACTUATED


def _span(first: int, last: int) -> str:
    """1-based step numbers, compact: ``3`` or ``3–5``."""
    return str(first) if first == last else str(first) + "–" + str(last)


def _summary(entry: dict | None, index: int, span: str) -> str:
    """``stopped at step 3 (missing_element: 'Login'); not run: 3–5``."""
    outcome = str((entry or {}).get("outcome") or "stopped")
    detail = str((entry or {}).get("detail") or "")[:80]
    why = outcome + (": " + detail if detail else "")
    return "stopped at step " + str(index + 1) + " (" + why + "); not run: " + span


def with_not_run(run: _ReplayRun, reply: dict, index: int) -> dict:
    """*reply*, plus ``not_run`` when the script stopped with actions left over."""
    content: Any = reply.get("content")
    if not isinstance(content, dict) or content.get("budget_stop"):
        return reply
    entry = _stopped_entry(run, index)
    start = index + 1 if _ran(entry) else index
    if start >= len(run.items):
        return reply
    note = _summary(entry, index, _span(start + 1, len(run.items)))
    reason = str(content.get("reason") or "")
    listed = _serialize_queued(run.items[start:])
    content["not_run"] = listed
    content["not_run_from"] = start + 1
    # A credential action with text ends the list (the host writes the rest again).
    content["not_run_truncated"] = len(listed) < len(run.items) - start
    content["reason"] = (reason + " (" + note + ")") if reason else note
    return reply
