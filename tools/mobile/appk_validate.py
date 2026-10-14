"""Validating a whole knowledge note: text, kind, ``when`` and ``then``."""

from __future__ import annotations

from typing import Any

from tools.mobile import appk_consts
from tools.mobile.appk_then import _validate_steps, _validate_then
from tools.mobile.appk_when import _validate_when


def _clean_text(text: Any) -> tuple:
    if not isinstance(text, str) or not text.strip():
        return "", "the note text is empty"
    text = " ".join(text.split())
    if len(text) > appk_consts.MAX_NOTE_CHARS:
        return "", "the note text is over %d characters" % (appk_consts.MAX_NOTE_CHARS)
    return text, ""


def _validate(parts: tuple) -> tuple | str:
    """``(text, when, then)`` cleaned, or an error string. *parts* is
    ``(text, kind, when, then)``."""
    text, kind, when, then = parts
    text, problem = _clean_text(text)
    if problem:
        return problem
    if kind not in appk_consts.KINDS:
        return "kind must be one of: " + ", ".join(appk_consts.KINDS)
    when = {} if when is None else when
    then = {} if then is None else then
    if not isinstance(when, dict):
        return "when must be an object"
    if not isinstance(then, dict):
        return "then must be an object"
    clean_when, problem = _validate_when(when)
    if problem:
        return problem
    return _validate_by_kind(kind, (text, clean_when), then)


def _bare_problem(kind: str, clean_when: dict, then: dict) -> str:
    if kind == "avoid" and "rid" not in clean_when:
        return "an avoid note needs when.rid"
    if then:
        article = "an" if kind == "avoid" else "a"
        return "%s %s note takes no `then`" % (article, kind)
    return ""


def _validate_by_kind(kind: str, body: tuple, then: dict) -> tuple | str:
    """The kind-specific part of ``_validate``; *body* is ``(text, clean_when)``."""
    text, clean_when = body
    if kind in ("fact", "avoid"):
        return _bare_problem(kind, clean_when, then) or (text, clean_when, {})
    if kind == "precondition":
        steps, problem = _validate_steps(then)
        return problem or (text, clean_when, {"steps": steps})
    target = "rid" in clean_when or "activity" in clean_when
    clean_then, problem = _validate_then(then, target)
    return problem or (text, clean_when, clean_then)
