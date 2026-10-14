"""Plain-language words for a trace action and a step row."""

from __future__ import annotations

from typing import Any

from tools.mobile.report_text import _ms, _text, esc, fmt_ms
from tools.mobile.rfrm_look import _resolve_look
from tools.mobile.rtrc_action import _action_line, _typed_literal


def _q(text: object) -> tuple:
    """A quoted run of app text inside a plain-words line."""
    return ("q", _text(text, 80))


def _rid_tail(rid: object) -> str:
    return str(rid or "").rsplit("/", 1)[-1].replace("_", " ")


_TARGET_VERBS = {
    "tap": "Tapped ",
    "long_press": "Long-pressed ",
    "clear": "Cleared ",
}


_FIXED_WORDS = {
    "wait_until_changed": "Waited for the screen to change",
    "wait_until_idle": "Waited for the screen to settle",
    "launch": "Opened the app",
}


_WAIT_OPS = ("wait", "wait_until_text", "wait_until_gone")


_TURN_OPS = ("assert", "done", "ask_tester")


def _named_target(target: object) -> tuple:
    """The target's quoted name, its role, and the words that point at it."""
    target = target if isinstance(target, dict) else {}
    name = _text(
        target.get("text")
        or target.get("label")
        or target.get("desc")
        or _rid_tail(target.get("rid")),
        60,
    )
    role = _text(target.get("role"), 24)
    thing = [_q(name)] if name else ["the " + role if role else "an unlabelled element"]
    return name, role, thing


def _plain_wait(op: str, body: dict) -> list:
    """The plain words of the three waits that name a text or a time."""
    if op == "wait_until_text":
        return ["Waited for ", _q(_text(body.get("text"), 80)), " to appear"]
    if op == "wait_until_gone":
        return ["Waited for ", _q(_text(body.get("text"), 80)), " to go"]
    until = _text(body.get("until_text"), 80)
    if until:
        return ["Waited for ", _q(until), " to appear"]
    return ["Waited " + fmt_ms(_ms(body.get("ms")) or 0)]


def _plain_turn_op(op: str, body: dict) -> list:
    """The plain words of the ops that end or check a turn."""
    if op == "assert":
        text = _text(body.get("text") or body.get("contains"), 80)
        if text:
            return ["Checked that ", _q(text), " is on screen"]
        return ["Checked the screen"]
    if op == "done":
        reason = _text(body.get("reason"), 200)
        if reason:
            return ["Finished the turn: ", _q(reason)]
        return ["Finished the turn"]
    field = _text(body.get("field"), 24)
    return ["Asked the tester for " + (field or "input")]


def _plain_nav(op: str, body: dict) -> list | None:
    """The plain words of scrolls, swipes and the back and home presses, else None."""
    if op in ("scroll", "swipe"):
        return ["Scrolled " + (_text(body.get("dir"), 12) or "the screen")]
    if op in ("back", "home"):
        return ["Pressed " + op.capitalize()]
    return None


# Typed text goes through :func:`_typed_literal`, so a credential is the
# mask here exactly as it is in the step table.
def _plain_action(action: object) -> list:
    """A trace action as plain words: text parts and :func:`_q` quotes."""
    body = action if isinstance(action, dict) else {}
    op = str(body.get("op") or "")
    name, role, thing = _named_target(body.get("target"))
    if op in _TARGET_VERBS:
        return [_TARGET_VERBS[op]] + thing
    if op in _FIXED_WORDS:
        return [_FIXED_WORDS[op]]
    if op == "type":
        typed = _typed_literal(body)
        return ["Typed ", _q(typed)] + ([" into "] + thing if name or role else [])
    if (nav := _plain_nav(op, body)) is not None:
        return nav
    if op in _WAIT_OPS:
        return _plain_wait(op, body)
    if op in _TURN_OPS:
        return _plain_turn_op(op, body)
    return [_action_line(body)]


def _parts_html(parts: Any) -> str:
    out = []
    for part in list(parts or []):
        if isinstance(part, tuple):
            out.append('<q dir="auto">' + esc(part[1], 200) + "</q>")
        else:
            text = str(part)
            out.append(
                (" " if text[:1] == " " else "")
                + esc(text, 200)
                + (" " if text[-1:] == " " else "")
            )
    return "".join(out)


def _element_of(target_id: object, screen: object) -> dict:
    tid = str(target_id or "")
    body = screen if isinstance(screen, dict) else {}
    for element in list(body.get("elements") or []) if tid else []:
        if isinstance(element, dict) and str(element.get("id") or "") == tid:
            return element
    return {}


# The row's plain words, naming its target from the before screen when
# the trace itself recorded only the element's id.
def _step_words(row: dict, library: object) -> list:
    """The row's plain words, naming its target from the before screen when needed."""
    action: Any = row.get("act") if isinstance(row.get("act"), dict) else {}
    target = action.get("target") if isinstance(action.get("target"), dict) else {}
    if not target or any(target.get(k) for k in ("text", "label", "desc", "rid")):
        return row.get("plain") or []
    _ident, screen, _verdict = _resolve_look(
        row.get("before_obs") or row.get("before"), library
    )
    element = _element_of(target.get("id"), screen)
    if not element:
        return row.get("plain") or []
    named = {k: element.get(k) for k in ("text", "label", "desc", "rid", "role")}
    return _plain_action(dict(action, target=dict(target, **named)))
