"""Which elements each supplied selector matches on a pruned screen."""

from __future__ import annotations

from tools.mobile.act_select import (
    _labelled_candidates,
    _norm,
    _rid_candidates,
    _selector_values,
    _text_candidates,
)
from tools.mobile.action_consts import TAP_TEXT_MAX_QUERY_CHARS
from tools.mobile.action_models_basic import Target


def _screen_elements(pruned: object) -> list:
    """The element dicts of *pruned*, whatever shape a caller sent."""
    screen = pruned if isinstance(pruned, dict) else {}
    if isinstance(screen.get("content"), dict):
        screen = screen["content"]
    # A new PUBLIC entry point, and the property test calls it directly, so it
    # takes whatever a caller sends: 16 of 112 junk screens raised TypeError
    # here before this line.
    raw = screen.get("elements")
    return [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []


def _group_for(name: str, want: str, elements: list) -> tuple:
    """``(how, matches)`` for one selector."""
    if name == "rid":
        return ("rid", _rid_candidates(elements, want))
    if name == "role":
        return ("role", [e for e in elements if _norm(e.get("role")) == want])
    if name == "label":
        return _labelled_candidates(elements, "label", want)
    if name == "text":
        return _text_candidates(elements, want)
    return ("id", [e for e in elements if _norm(e.get("id")) == want])


# Public because :func:`resolve_target` is implemented on top of it and a
# property test enumerates ``Target.model_fields`` against it. That is the
# mechanical half of the agreement rule: the rule iterates whatever this
# returns, so it cannot forget a selector the way an id-only check did -- and
# when ``role`` and ``label`` arrived from another branch, the test named them
# rather than letting them through unchecked.
def candidates_for(target: object, pruned: object) -> dict:
    """``{selector: (how, [every element that selector ALONE matches])}``."""
    elements = _screen_elements(pruned)
    out: dict = {}
    for name, want in _selector_values(target).items():
        out[name] = _group_for(name, want, elements)
    return out


#: What `tap_text` answers when the word is on more than one control. A SENTINEL
#: with one meaning and one producer: the executor renders it and the trace
#: carries it, so both branch on this constant rather than on a substring of a
#: sentence.
TAP_TEXT_AMBIGUOUS = "tap_text_ambiguous"


def tap_text_candidates(text: object, pruned: object) -> tuple:
    """``(target, [every element carrying this word])``.

    The VOCABULARY half of `tap_text`, and only that half. How many CONTROLS
    those elements amount to is a question about the screen's containment, and
    that has one producer already -- `executor.actuated_element`, the function
    the destructive guard is judged by. Deriving containment a second time here
    is how two call sites start disagreeing about one screen.
    """
    wanted = str(text or "").strip()
    if not wanted:
        return None, []
    target = Target(text=wanted[:TAP_TEXT_MAX_QUERY_CHARS])
    found = candidates_for(target, pruned) or {}
    _how, group = found.get("text", ("text", []))
    return target, [e for e in (group or []) if isinstance(e, dict)]
