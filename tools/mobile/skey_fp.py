"""Fingerprint leaf of screen_key: the text-light shape of one element."""

from __future__ import annotations

import hashlib
import re
from typing import Any

_DIGITS_RE = re.compile(r"\d")
#: An address-shaped token: kept as "@" so a fingerprint never stores the address.
_EMAIL_RE = re.compile(r"\S+@\S+")
_TEXT_SHAPE_CHARS = 40
_ANCESTORS_KEPT = 4
_POS_COL_PX = 360
_POS_ROW_PX = 640


def _sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]


def _bounds(element: dict) -> tuple | None:
    raw = element.get("bounds")
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        try:
            return tuple(int(v) for v in raw)
        except (TypeError, ValueError, OverflowError):
            return None
    return None


def text_shape(text: object) -> str:
    """Lowercase, digits to ``#``, addresses to ``@``, clipped: the shape of a
    label, not its value."""
    flat = _EMAIL_RE.sub("@", " ".join(str(text or "").split()).lower())
    shaped = _DIGITS_RE.sub("#", flat)
    return shaped[:_TEXT_SHAPE_CHARS]


def _pos_bucket(node: dict) -> str:
    box = _bounds(node)
    if not box:
        return ""
    return "%d,%d" % (
        ((box[0] + box[2]) // 2) // _POS_COL_PX,
        ((box[1] + box[3]) // 2) // _POS_ROW_PX,
    )


def _ancestor_rids(ancestors: Any) -> list:
    return [
        str(a.get("rid") or "")
        for a in (ancestors or ())
        if isinstance(a, dict) and a.get("rid")
    ]


def _empty_fp() -> dict:
    return {
        "rid": "",
        "desc": "",
        "text_shape": "",
        "cls": "",
        "anc_rids": [],
        "pos_bucket": "",
    }


def element_fp(node: object, ancestors: Any = ()) -> dict:
    """A text-light fingerprint of one element. Never raises."""
    try:
        node = node if isinstance(node, dict) else {}
        return {
            "rid": str(node.get("rid") or ""),
            # Shaped like text: fp_json is structural (never secret-scanned),
            # so no raw value may reach it.
            "desc": text_shape(node.get("desc")),
            "text_shape": text_shape(node.get("text")),
            "cls": str(node.get("cls") or ""),
            "anc_rids": _ancestor_rids(ancestors)[:_ANCESTORS_KEPT],
            "pos_bucket": _pos_bucket(node),
        }
    except (ValueError, TypeError, KeyError, AttributeError):
        return _empty_fp()


def fp_hash(fp: Any) -> str:
    """Stable hash of a fingerprint dict (key order never matters)."""
    try:
        parts = []
        for name in sorted(fp):
            parts.append("%s=%s" % (name, fp[name]))
        return _sha("|".join(parts))
    except (ValueError, TypeError, KeyError, AttributeError):
        return _sha("")
