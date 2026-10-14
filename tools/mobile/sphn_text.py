"""Text, box and speaker helpers shared by the phone drawing (leaf of screen_phone)."""

from __future__ import annotations

import re
from typing import Any

MAX_TEXT = 220

#: Speaker labels the composer recognises on a container's accessibility label. Generic
#: English forms; a profile may not add to them (no vendor word lives in tools/mobile).
USER_PREFIXES = ("you said:", "you:", "user:", "you typed:")
ASSISTANT_PREFIXES = ("assistant replied:", "assistant:", "replied:", "bot:", "reply:")

# The Arabic block, written as escapes: the reference-leak gate forbids any RTL
# codepoint in this tree's source, and the regex engine reads both forms alike.
_ARABIC = re.compile("[\u0600-\u06ff]")


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _box(element: dict) -> tuple | None:
    raw = element.get("bounds")
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    values: list[Any] = [_int(v) for v in raw]
    if any(v is None for v in values):
        return None
    left, right = sorted((values[0], values[2]))
    top, bottom = sorted((values[1], values[3]))
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def _inside(inner: tuple, outer: tuple) -> bool:
    return (
        inner[0] >= outer[0] - 2
        and inner[1] >= outer[1] - 2
        and inner[2] <= outer[2] + 2
        and inner[3] <= outer[3] + 2
    )


def _text(element: dict) -> str:
    return " ".join(str(element.get("text") or "").split())[:MAX_TEXT]


def _desc(element: dict) -> str:
    return " ".join(str(element.get("desc") or "").split())[:MAX_TEXT]


def _rtl(texts: list) -> bool:
    joined = " ".join(texts)
    letters = sum(1 for ch in joined if ch.isalpha())
    return letters > 0 and len(_ARABIC.findall(joined)) * 2 > letters


def _speaker(label: str) -> str:
    low = label.lower()
    if any(low.startswith(p) for p in USER_PREFIXES):
        return "user"
    if any(low.startswith(p) for p in ASSISTANT_PREFIXES):
        return "assistant"
    return ""


def _elements(screen: object) -> list:
    body = screen if isinstance(screen, dict) else {}
    out = []
    for element in body.get("elements") or []:
        if not isinstance(element, dict):
            continue
        box = _box(element)
        if box is None:
            continue
        out.append((box, element))
    return out
