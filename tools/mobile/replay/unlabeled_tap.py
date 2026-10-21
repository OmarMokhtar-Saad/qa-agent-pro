"""A tap on a control with no name: say so in the step reply, and what it opened."""

from __future__ import annotations

from typing import Any

from tools.mobile.replay.elements_label import element_label
from tools.mobile.replay.tap_ambiguity import note_if_ambiguous
from tools.mobile.saud_names import _rid_name

#: Words of a screen that is a live call, in the Latin script. A tap that lands
#: on one has started something the host did not mean to start.
CALL_WORDS = frozenset({"calling", "ringing", "dialing", "dialling", "end call"})
TAP_OPS = ("tap", "tap_text", "tap_text_if_present")
_TOP_TEXTS = 4
#: The guard term of a tap on a control that has no name (a lexicon entry, so
#: the stop is confirmable like any other control stop).
UNNAMED_TERM = "unnamed control"


def unlabeled_hit(op: str, node: Any, screen: Any = None) -> str:
    """:data:`UNNAMED_TERM` when a tap lands on a node with no text,
    description, contained text or id name; else ``""``."""
    if op not in TAP_OPS:
        return ""
    return UNNAMED_TERM if is_unlabeled(node, screen) else ""


def is_unlabeled(element: Any, screen: Any = None) -> bool:
    """True for a control with no text, desc, contained text or id name."""
    if not isinstance(element, dict):
        return False
    if element.get("label") or _rid_name(element.get("rid")):
        return False
    words = element_label(dict(element, cls="", rid=""), screen)  # text/desc/children
    return not words


def names_target(element: Any, screen: Any = None) -> bool:
    """True when the step's own element carries a name (text, desc, id name)."""
    return isinstance(element, dict) and not is_unlabeled(element, screen)


def _texts(screen: Any) -> list[str]:
    rows = screen.get("elements") if isinstance(screen, dict) else None
    texts = [
        str(e.get("text") or "").strip() for e in rows or [] if isinstance(e, dict)
    ]
    return [t[:40] for t in texts if t]


def opened(screen: Any) -> str:
    """What the screen after a tap shows: its app and first words."""
    if not isinstance(screen, dict) or not screen.get("elements"):
        return "an empty screen read"
    shown = "; ".join(_texts(screen)[:_TOP_TEXTS]) or "no text"
    return f"package {screen.get('package') or '?'}, showing: {shown}"


def call_like(screen: Any) -> bool:
    """Whether the screen's own words say a call is under way."""
    low = " ".join(_texts(screen)).lower()
    return any(word in low for word in CALL_WORDS)


def advice(element: Any, after: Any) -> str:
    """The host-facing line for a tap on an unlabeled control."""
    line = (
        f"Tapped unlabeled control #{element.get('id')} (no text, description or "
        f"id name); the tap opened {opened(after)}."
    )
    if call_like(after):
        line += " That screen looks like a CALL: end it and ask before retrying."
    return line + " Prefer a named target; ask the tester before tapping unnamed ones."


def note_if_unlabeled(entry: dict, step: Any, shot: tuple) -> None:
    """Append the advice to a tap entry that hit an unlabeled control.

    *shot* is ``(screen before, screen after)``."""
    element = getattr(step, "element", None)
    if step.op not in TAP_OPS or entry.get("outcome") == "error":
        return
    if is_unlabeled(element, shot[0]):
        said = f"{entry.get('detail') or ''} {advice(element, shot[1])}"
        entry["detail"] = said.strip()


def note_tap_advice(entry: dict, step: Any, shot: tuple) -> None:
    """Ambiguity and unlabeled-control notes for one settled tap."""
    note_if_ambiguous(entry, step, shot[0])
    note_if_unlabeled(entry, step, shot)
