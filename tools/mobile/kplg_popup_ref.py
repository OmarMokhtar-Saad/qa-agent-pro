"""Popup dismiss references: a resource id, or ``text:<label>`` for a label-only button.

A system or app sheet ("No, Later", "Not Allow") often has no resource id, so the
dismiss is remembered by its visible label. Labels are UI strings, never typed values.
"""

from __future__ import annotations

import re
from typing import Any, cast

TEXT = "text:"
MAX_LABEL = 80
#: Ops whose ``text`` is a typed value (or a key), never a label to tap.
_VALUE_OPS = ("type", "fill", "press")
_DISMISS = re.compile(
    r"\b(later|not now|not allow|don'?t allow|no thanks|deny|dismiss|not interested)\b",
    re.IGNORECASE,
)


def body_of(step: Any) -> dict:
    """An action model or dict as a plain dict."""
    if isinstance(step, dict):
        return step
    dump = getattr(step, "model_dump", None)
    return dump(mode="json") if dump else {}


def _target(body: dict) -> dict:
    return cast("dict", body["target"] if isinstance(body.get("target"), dict) else {})


def _clean(raw: Any) -> str:
    return " ".join(str(raw or "").split())[:MAX_LABEL]


def label_of(step: Any) -> str:
    """The visible label a tap names (target text/label, or a ``tap_text`` text)."""
    body = body_of(step)
    if body.get("op") in _VALUE_OPS:
        return ""
    target = _target(body)
    return _clean(
        target.get("text")
        or target.get("label")
        or body.get("text")
        or body.get("label")
    )


def text_ref(label: str) -> str:
    return TEXT + label if label else ""


def step_ref(step: Any) -> str:
    """The rid a step targets, else its ``text:`` reference, else ``""``."""
    body = body_of(step)
    rid = str(_target(body).get("rid") or body.get("rid") or "")
    return rid or text_ref(label_of(body))


def is_dismiss_label(label: Any) -> bool:
    return bool(_DISMISS.search(str(label or "")))


def screen_refs(screen: Any) -> set:
    """``text:`` references of every labelled element on *screen*."""
    out: set = set()
    elements = screen.get("elements") if isinstance(screen, dict) else None
    for element in elements if isinstance(elements, list) else []:
        if isinstance(element, dict):
            label = _clean(element.get("text") or element.get("label"))
            if label:
                out.add(text_ref(label))
    return out


def target_of(ref: str) -> dict:
    """The action target that taps *ref*."""
    if ref.startswith(TEXT):
        return {"text": ref[len(TEXT) :]}
    return {"rid": ref}
