"""The visible record of auto-replayed steps (see ``flow_replay_run``).

Each replayed step's trace entry is marked ``replay`` so a reader tells it from a step
the model chose. A step that answered a system dialog also carries ``popup``: the
button tapped and the text the dialog showed, read from the live screen before the tap.
Pure; never raises.
"""

from __future__ import annotations

from typing import Any

from tools.mobile import flow_replay_dialog, kstg_flow_perm
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
    """The text of the system dialog on *screen* (all visible text when none is tagged)."""
    raw = screen.get("elements") if isinstance(screen, dict) else None
    every = [e for e in raw or [] if isinstance(e, dict)]
    tagged = [e for e in every if e.get("package") in SYSTEM_DIALOG_PACKAGES]
    return " | ".join(_words(tagged or every))[:MAX_TEXT_CHARS]


def mark(trace: list, steps: list, seen: dict) -> list:
    """*trace* with every entry marked ``replay``; dialog answers also get ``popup``.

    *steps* are the template steps of the batch, in run order; *seen* maps a batch
    index to the dialog text read before that step.
    """
    out = []
    for i, entry in enumerate(trace):
        marked = dict(entry, replay=True)
        step = steps[i] if i < len(steps) else {}
        if flow_replay_dialog.answers_dialog(step):
            button = kstg_flow_perm.label(step)
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
