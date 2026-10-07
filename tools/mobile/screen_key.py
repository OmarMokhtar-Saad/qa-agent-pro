"""A stable identity for a screen, built from structure and never from text.

Pure, no I/O, never raises. ``screen_key`` hashes the package, the activity and
the resource ids that occur exactly once on the screen (the *anchors*), so three
chat screens that differ only in message text share one key. A screen with fewer
than two anchors gets a *degraded* key (class counts); learning treats such keys
coarsely and never injects lessons from them.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass

from tools.mobile import knowledge_limits

#: A resource id that is an instance, not a place: digit suffix or list-row names.
DYNAMIC_RID_RE = re.compile(
    r"(\d+$|(^|[_/])(item|row|cell)_|message|bubble)", re.IGNORECASE
)
_LIST_CLASS_RE = re.compile(r"(recycler|listview|gridview|scrollview|pager)", re.I)
_DIGITS_RE = re.compile(r"\d")
#: An address-shaped token: kept as "@" so a fingerprint never stores the address.
_EMAIL_RE = re.compile(r"\S+@\S+")
_TEXT_SHAPE_CHARS = 40
_ANCESTORS_KEPT = 4
_POS_COL_PX = 360
_POS_ROW_PX = 640


@dataclass(frozen=True)
class ScreenKey:
    key: str
    activity: str
    anchors: tuple
    title: str
    degraded: bool


def _sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]


def _strip_package(rid: str, package: str) -> str:
    if package and rid.startswith(package + ":"):
        rid = rid[len(package) + 1 :]
    return rid[3:] if rid.startswith("id/") else rid


def _bounds(element: dict) -> tuple | None:
    raw = element.get("bounds")
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        try:
            return tuple(int(v) for v in raw)
        except (TypeError, ValueError, OverflowError):
            return None
    return None


def _inside(inner: tuple, outer: tuple) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
        and inner != outer
    )


def _list_boxes(elements: list) -> list:
    out = []
    for element in elements:
        box = _bounds(element)
        if box and (
            element.get("scrollable") or _LIST_CLASS_RE.search(str(element.get("cls")))
        ):
            out.append(box)
    return out


def _anchors(elements: list, package: str) -> tuple:
    boxes = _list_boxes(elements)
    counts: Counter = Counter()
    for element in elements:
        rid = _strip_package(str(element.get("rid") or "").strip(), package)
        if not rid:
            continue
        box = _bounds(element)
        in_list = bool(box) and any(_inside(box, outer) for outer in boxes)
        counts[rid] += 1 if not in_list else 2
    anchors = sorted(
        rid for rid, n in counts.items() if n == 1 and not DYNAMIC_RID_RE.search(rid)
    )
    return tuple(anchors[: knowledge_limits.MAX_ANCHORS])


def _title(elements: list) -> str:
    for element in elements:
        rid = str(element.get("rid") or "").lower()
        if "title" in rid and element.get("text"):
            return text_shape(element.get("text"))
    return ""


def text_shape(text: object) -> str:
    """Lowercase, digits to ``#``, addresses to ``@``, clipped: the shape of a
    label, not its value."""
    flat = _EMAIL_RE.sub("@", " ".join(str(text or "").split()).lower())
    shaped = _DIGITS_RE.sub("#", flat)
    return shaped[:_TEXT_SHAPE_CHARS]


def _degraded(package: str, activity: str, elements: list) -> str:
    classes = Counter(str(e.get("cls") or "") for e in elements)
    body = ",".join("%s:%d" % (k, classes[k]) for k in sorted(classes))
    return "sk1d:" + _sha("|".join((package, activity, body)))


def screen_key(screen: object, package: str = "", activity: str = "") -> ScreenKey:
    """The key of a perception screen dict. Never raises."""
    try:
        body = screen if isinstance(screen, dict) else {}
        package = str(package or body.get("package") or "")
        activity = str(activity or body.get("activity") or "")
        raw = body.get("elements")
        elements = [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []
        anchors = _anchors(elements, package)
        title = _title(elements)
        if len(anchors) < 2:
            key = _degraded(package, activity, elements)
            return ScreenKey(key, activity, anchors, title, True)
        key = "sk1:" + _sha("|".join((package, activity, "|".join(anchors))))
        return ScreenKey(key, activity, anchors, title, False)
    except (ValueError, TypeError, KeyError, AttributeError):
        return ScreenKey("sk1d:" + _sha("error"), "", (), "", True)


def similarity(a_anchors: object, b_anchors: object) -> float:
    """Jaccard overlap of two anchor collections; 0.0 when both are empty."""
    try:
        a, b = set(a_anchors or ()), set(b_anchors or ())
        union = a | b
        return len(a & b) / len(union) if union else 0.0
    except TypeError:
        return 0.0


def _pos_bucket(node: dict) -> str:
    box = _bounds(node)
    if not box:
        return ""
    return "%d,%d" % (
        ((box[0] + box[2]) // 2) // _POS_COL_PX,
        ((box[1] + box[3]) // 2) // _POS_ROW_PX,
    )


def element_fp(node: object, ancestors: object = ()) -> dict:
    """A text-light fingerprint of one element. Never raises."""
    try:
        node = node if isinstance(node, dict) else {}
        anc = [
            str(a.get("rid") or "")
            for a in (ancestors or ())
            if isinstance(a, dict) and a.get("rid")
        ]
        return {
            "rid": str(node.get("rid") or ""),
            # Shaped like text: fp_json is structural (never secret-scanned),
            # so no raw value may reach it.
            "desc": text_shape(node.get("desc")),
            "text_shape": text_shape(node.get("text")),
            "cls": str(node.get("cls") or ""),
            "anc_rids": anc[:_ANCESTORS_KEPT],
            "pos_bucket": _pos_bucket(node),
        }
    except (ValueError, TypeError, KeyError, AttributeError):
        return {
            "rid": "",
            "desc": "",
            "text_shape": "",
            "cls": "",
            "anc_rids": [],
            "pos_bucket": "",
        }


def fp_hash(fp: object) -> str:
    """Stable hash of a fingerprint dict (key order never matters)."""
    try:
        parts = []
        for name in sorted(fp):
            parts.append("%s=%s" % (name, fp[name]))
        return _sha("|".join(parts))
    except (ValueError, TypeError, KeyError, AttributeError):
        return _sha("")
