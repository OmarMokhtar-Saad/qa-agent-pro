"""A tap that changed nothing while several controls share its name: tell the host."""

from __future__ import annotations

from typing import Any

from tools.mobile.saud_names import _is_interactive, _rid_name

#: ``tap_text_if_present`` is left out: it is optional and picks among several
#: same-worded controls by design (present_pick), so "be more specific" misleads.
TAP_OPS = ("tap", "tap_text")


def _spoken(element: dict) -> str:
    return str(element.get("label") or "").strip() or _rid_name(element.get("rid"))


def _name_of(element: dict) -> str:
    """The lowercased spoken name of a control: its label, else its id's name."""
    return " ".join(_spoken(element).lower().split())


def same_named(screen: Any, element: Any) -> int:
    """How many interactive controls on *screen* answer to *element*'s name."""
    if not isinstance(screen, dict) or not isinstance(element, dict):
        return 0
    name = _name_of(element)
    rows = [e for e in (screen.get("elements") or []) if isinstance(e, dict)]
    return sum(1 for e in rows if name and _is_interactive(e) and _name_of(e) == name)


def advice(count: int, element: dict) -> str:
    """The host-facing line for *count* controls sharing the tapped one's name."""
    return (
        f"{count} controls are named {_spoken(element)[:60]!r}; the tap hit "
        f"#{element.get('id')} and nothing changed -- use a more specific target."
    )


#: Said on the tap's own reply: the settled read equals the one before the tap.
DEAD_TAP = (
    "The tap went through but the screen did not change; it may not have "
    "registered. Check the screen before relying on it."
)


def note_if_ambiguous(entry: dict, step: Any, before: Any) -> None:
    """Append the advice to a tap entry that did nothing: the ambiguity hint when
    several controls share the name, else the plain "screen did not change"
    (said only when the auto-settle proved it: a bare re-dump may just be early)."""
    if step.op not in TAP_OPS or entry.get("outcome") != "no_change":
        return
    element = getattr(step, "element", None)
    count = same_named(before, element)
    if count > 1 and isinstance(element, dict):
        said = advice(count, element)
    elif entry.get("settled_unchanged") is True:
        said = DEAD_TAP
    else:
        return
    entry["detail"] = f"{entry.get('detail') or ''} {said}".strip()
