"""Small value helpers shared by the readers and the stream arms."""

from __future__ import annotations

import json
from typing import Iterable

from tools.mobile_evidence.grammar_consts import (
    RE_BODY_HASHED,
    RE_BODY_TRUNC,
    RE_KV_BOUNDARY,
)


def _unflatten(text: str):
    """``msg k=v k=v`` -> (msg, {k: v}). Only ever applied to a structured category."""
    parts = RE_KV_BOUNDARY.split(text.strip())
    msg, fields = parts[0], {}
    for part in parts[1:]:
        key, _, value = part.partition("=")
        fields[key] = value
    return msg, fields


def _body_shape(text):
    """How much of a body survived the body policy -- never guess it was verbatim."""
    if text is None:
        return None
    m = RE_BODY_HASHED.match(text.strip())
    if m:
        return {
            "policy": "NONE",
            "length": int(m.group(1)),
            "hash": m.group(2),
            "text": None,
        }
    m = RE_BODY_TRUNC.search(text)
    if m:
        return {"policy": "TRUNCATED", "omitted": int(m.group(1)), "text": text}
    return {"policy": "FULL", "text": text}


def _maybe_json(text):
    if not text:
        return None
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        return json.loads(stripped)
    except ValueError:
        return None


def _int_or_none(value):
    if value in (None, "?", ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _has_rtl(text: str) -> bool:
    return any("\u0590" <= ch <= "\u08ff" for ch in text or "")


def _lines_of(source) -> Iterable[str]:
    """A path or an iterable of lines, read the same way."""
    if isinstance(source, str):
        with open(source, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                yield raw
        return
    for raw in source or []:
        yield str(raw)
