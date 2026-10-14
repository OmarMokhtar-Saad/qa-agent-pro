"""A stable identity for a screen, built from structure and never from text.

Pure, no I/O, never raises. ``screen_key`` hashes the package, the activity and
the resource ids that occur exactly once on the screen (the *anchors*), so three
chat screens that differ only in message text share one key. A screen with fewer
than two anchors gets a *degraded* key (class counts); learning treats such keys
coarsely and never injects lessons from them.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from tools.mobile import knowledge_limits
from tools.mobile.skey_fp import (  # noqa: F401
    _bounds,
    _sha,
    element_fp,
    fp_hash,
    text_shape,
)

#: A resource id that is an instance, not a place: digit suffix or list-row names.
DYNAMIC_RID_RE = re.compile(
    r"(\d+$|(^|[_/])(item|row|cell)_|message|bubble)", re.IGNORECASE
)
_LIST_CLASS_RE = re.compile(r"(recycler|listview|gridview|scrollview|pager)", re.I)


@dataclass(frozen=True)
class ScreenKey:
    key: str
    activity: str
    anchors: tuple
    title: str
    degraded: bool


def _strip_package(rid: str, package: str) -> str:
    if package and rid.startswith(package + ":"):
        rid = rid[len(package) + 1 :]
    return rid[3:] if rid.startswith("id/") else rid


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
        box: Any = _bounds(element)
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
        box: Any = _bounds(element)
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
        elements = (
            [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []
        )
        anchors = _anchors(elements, package)
        title = _title(elements)
        if len(anchors) < 2:
            key = _degraded(package, activity, elements)
            return ScreenKey(key, activity, anchors, title, True)
        key = "sk1:" + _sha("|".join((package, activity, "|".join(anchors))))
        return ScreenKey(key, activity, anchors, title, False)
    except (ValueError, TypeError, KeyError, AttributeError):
        return ScreenKey("sk1d:" + _sha("error"), "", (), "", True)


def similarity(a_anchors: Any, b_anchors: Any) -> float:
    """Jaccard overlap of two anchor collections; 0.0 when both are empty."""
    try:
        a, b = set(a_anchors or ()), set(b_anchors or ())
        union = a | b
        return len(a & b) / len(union) if union else 0.0
    except TypeError:
        return 0.0
