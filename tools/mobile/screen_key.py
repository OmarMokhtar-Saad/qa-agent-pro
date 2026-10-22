"""A stable identity for a screen, built from structure and never from text.

Version ``sk3``: the activity is NO LONGER part of the key. It was read once per
context and changed under a screen, so one screen got several keys.
The activity stays on :class:`ScreenKey` as metadata only (the degraded
``sk1d`` key drops it too). ``sk2`` keys (which hashed the activity) stay stored
and link to their ``sk3`` twin through ``screens.remap_of``; ``sk2_key`` rebuilds
one for saved flows.

Version ``sk2``: the key also folds in the input structure (``_structure``),
because screens whose only anchors are shared containers (a login form and a
code-entry screen, neither with resource ids) collided under ``sk1``. Stored
``sk1:`` keys are never matched again (relearned under the new key); degraded
keys keep the ``sk1d:`` prefix the rest of the code tests for.

Pure, no I/O, never raises.``screen_key`` hashes the package and
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


def _is_input(element: dict) -> bool:
    cls = str(element.get("cls") or "").lower()
    return bool(element.get("editable")) or "edittext" in cls


def _field_shape(element: dict) -> str:
    label = text_shape(element.get("hint")) + "/" + text_shape(element.get("desc"))
    return "%s:%s" % (element.get("cls"), label)


def _structure(elements: list) -> str:
    """The input structure outside lists: each input field's class and the shape
    of its hint/description, and how many controls are tappable. Never text or
    values, so a timer, a typed value or a message cannot move it."""
    boxes, fields, taps = _list_boxes(elements), [], 0
    for element in elements:
        box: Any = _bounds(element)
        if box and any(_inside(box, outer) for outer in boxes):
            continue
        taps += 1 if element.get("clickable") else 0
        if _is_input(element):
            fields.append(_field_shape(element))
    return "in=%s;tap=%d" % (",".join(sorted(fields)), taps)


def _title(elements: list) -> str:
    for element in elements:
        rid = str(element.get("rid") or "").lower()
        if "title" in rid and element.get("text"):
            return text_shape(element.get("text"))
    return ""


def _degraded(package: str, elements: list, activity: str = "") -> str:
    classes = Counter(str(e.get("cls") or "") for e in elements)
    body = ",".join("%s:%d" % (k, classes[k]) for k in sorted(classes))
    return "sk1d:" + _sha("|".join((package, activity, body)))


def _elements(body: dict) -> list:
    raw = body.get("elements")
    return [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []


def _build(elements: list, package: str, activity: str) -> ScreenKey:
    anchors = _anchors(elements, package)
    title = _title(elements)
    if len(anchors) < 2:
        key = _degraded(package, elements)
        return ScreenKey(key, activity, anchors, title, True)
    parts = (package, "|".join(anchors), _structure(elements))
    key = "sk3:" + _sha("|".join(parts))
    return ScreenKey(key, activity, anchors, title, False)


def screen_key(screen: object, package: str = "", activity: str = "") -> ScreenKey:
    """The key of a perception screen dict. Never raises."""
    try:
        body = screen if isinstance(screen, dict) else {}
        package = str(package or body.get("package") or "")
        activity = str(body.get("activity") or activity or "")
        return _build(_elements(body), package, activity)
    except (ValueError, TypeError, KeyError, AttributeError):
        return ScreenKey("sk1d:" + _sha("error"), "", (), "", True)


def sk2_key(screen: object, package: str, activity: str) -> str:
    """The pre-sk3 key of *screen* read under *activity* (saved flows hold these)."""
    elements = _elements(screen if isinstance(screen, dict) else {})
    anchors = _anchors(elements, package)
    if len(anchors) < 2:
        return _degraded(package, elements, activity)
    parts = (package, activity, "|".join(anchors), _structure(elements))
    return "sk2:" + _sha("|".join(parts))


def legacy_from(package: str, activity: str, anchors: Any) -> str:
    """The ``sk1`` key a screen had before the input structure joined the hash."""
    parts = (str(package), str(activity), "|".join(str(a) for a in anchors or ()))
    return "sk1:" + _sha("|".join(parts))


def legacy_key(screen: object, package: str = "", activity: str = "") -> str:
    """The old ``sk1`` key of a perception screen, ``""`` when it is degraded."""
    sk = screen_key(screen, package, activity)
    if sk.degraded:
        return ""
    body = screen if isinstance(screen, dict) else {}
    package = str(package or body.get("package") or "")
    return legacy_from(package, sk.activity, sk.anchors)


def similarity(a_anchors: Any, b_anchors: Any) -> float:
    """Jaccard overlap of two anchor collections; 0.0 when both are empty."""
    try:
        a, b = set(a_anchors or ()), set(b_anchors or ())
        union = a | b
        return len(a & b) / len(union) if union else 0.0
    except TypeError:
        return 0.0
