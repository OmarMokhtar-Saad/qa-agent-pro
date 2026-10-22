"""The visible record of auto-replayed steps (see ``flow_replay_run``).

Each replayed step's trace entry is marked ``replay`` so a reader tells it from a step
the model chose. A step that answered a system dialog also carries ``popup``: the
button tapped and the text the dialog showed, read from the live screen before the tap.
Pure; never raises.
"""

from __future__ import annotations

from typing import Any

from tools.mobile import flow_replay_dialog
from tools.mobile.replay.screen_pkg import SYSTEM_DIALOG_PACKAGES

MAX_TEXT_CHARS = 200
SAID = "answered a system dialog (%s) by tapping `%s`"


def _words(elements: list) -> list:
    out: list = []
    for element in elements:
        text = " ".join(str(element.get("text") or element.get("desc") or "").split())
        if text and text not in out:
            out.append(text)
    return out


def dialog_text(screen: Any) -> str:
    """The text of the system dialog on *screen* (``""`` when none is tagged: never other text)."""
    raw = screen.get("elements") if isinstance(screen, dict) else None
    every = [e for e in raw or [] if isinstance(e, dict)]
    tagged = [e for e in every if e.get("package") in SYSTEM_DIALOG_PACKAGES]
    return " | ".join(_words(tagged))[:MAX_TEXT_CHARS]


def mark(trace: list, steps: list, seen: dict) -> list:
    """*trace* with every entry marked ``replay``; dialog answers also get ``popup``.

    *steps* are the template steps of the batch, in run order; *seen* maps a batch
    index to the dialog text read before that step.
    """
    out = []
    for i, entry in enumerate(trace):
        marked = dict(entry, replay=True)
        step = steps[i] if i < len(steps) else {}
        button = str(entry.get("tapped") or "")
        ok = entry.get("outcome") == "ok" and bool(button)
        if ok and flow_replay_dialog.answers_dialog(step):
            marked["popup"] = {"button": button, "text": seen.get(i, "")}
        out.append(marked)
    return out


def popups(trace: list) -> list:
    """The ``popup`` records of *trace*, in order."""
    return [t["popup"] for t in trace if isinstance(t, dict) and t.get("popup")]


def said(trace: list) -> list:
    """The note lines for the dialogs the replay answered (text quoted when known)."""
    return [
        SAID % (p.get("text") or "text unread", p.get("button")) + "."
        for p in popups(trace)
    ]


def watch(gate: Any) -> Any:
    """*gate* that also keeps, per step index, the dialog text it saw (``.seen``)."""

    async def watched(index: int, screen: Any) -> tuple:
        latest, why = await gate(index, screen)
        watched.seen[index] = dialog_text(latest)  # type: ignore[attr-defined]
        return latest, why

    watched.seen = {}  # type: ignore[attr-defined]
    return watched


#: The trace key that marks the entry a saved-flow replay stopped on (1-based step).
STOPPED_AT = "replay_stopped_at"


def mark_stop(trace: list, pos: int, why: str) -> list:
    """*trace* with its last entry marked as where the replay stopped (not for an owed input)."""
    if not trace or not why:
        return trace
    return trace[:-1] + [dict(trace[-1], **{STOPPED_AT: pos + 1})]


def stopped_at(case: object) -> int:
    """The step a saved-flow replay stopped at in *case*'s trace, or 0."""
    trace = case.get("trace") if isinstance(case, dict) else None
    marks = [
        int(t[STOPPED_AT])
        for t in (trace if isinstance(trace, list) else [])
        if isinstance(t, dict) and isinstance(t.get(STOPPED_AT), int)
    ]
    return marks[-1] if marks else 0
