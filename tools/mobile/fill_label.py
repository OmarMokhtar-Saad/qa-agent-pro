"""Find the input field a visible LABEL names -- the resolver behind ``fill``.

A pure function over the pruned element list (``perception.prune``): no device,
no I/O, no model. ``executor`` calls :func:`resolve_fill` the way it calls
``resolve_tap_text`` -- BEFORE the destructive guard, so the guard judges the
node a fill will touch exactly as it judges a tap.

Layers, tried in order within each match tier (below). The first (tier, layer)
holding a candidate decides; exactly one candidate wins, more than one is a
REFUSAL that names them.

1. ``field``    -- an EDITABLE node whose ``hint``, ``text`` or ``desc`` matches.
2. (no layer)   -- Android's ``labelFor`` pointer. A ``uiautomator dump`` does
   not write it (AOSP's ``AccessibilityNodeInfoDumper`` emits index, text,
   resource-id, class, package, content-desc, the boolean flags, bounds and,
   on newer releases, ``hint``), and ``perception.prune`` therefore never sees
   it. The layer is omitted rather than stubbed: a branch with no producer is
   dead code that reads as a capability.
3. ``adjacent`` -- the nearest EDITABLE node below, or to the right of, a
   non-editable node whose ``text`` or ``desc`` matches.

Matching is tiered ACROSS the layers: EXACT after normalisation (lower-case,
whitespace collapsed) in every layer first, then CONTAINS. "Contains" means the label's WORDS
appear as a contiguous run in the field's string -- never a raw substring, so
``Name`` does not select ``Username`` -- and only in that direction: a field
called ``Email`` is not selected by the label ``Email address``. Exact goes
first because a form holding both ``Name`` and ``Name of your pet`` must not
call ``Name`` ambiguous.

Nothing here quotes on-screen TEXT back: a refusal describes a candidate by its
resource id, class and bounds, so a field's current contents (which may be a
typed credential) and an attacker-written label never reach the model through
this module.
"""

from __future__ import annotations

import re

from tools.untrusted import single_line, wrap_untrusted

#: The longest label ``fill`` accepts. A LABEL, not a paragraph: it is matched
#: against the strings on a form field, so one longer than any of them can only
#: miss. It bounds the string a model can put into the matcher.
FILL_MAX_LABEL_CHARS = 120

#: How many candidates a refusal names. The model needs enough to pick a
#: narrower target; the rest are counted, not listed.
FILL_MAX_CANDIDATES_LISTED = 5

#: Characters of one candidate's description in a refusal. Resource ids are the
#: longest part and are attacker-influenced, so each is clipped.
FILL_MAX_CANDIDATE_CHARS = 60

LAYER_FIELD = "field"
LAYER_ADJACENT = "adjacent"
LAYERS = (LAYER_FIELD, LAYER_ADJACENT)

TIER_EXACT = "exact"
TIER_CONTAINS = "contains"

_WORDS = re.compile(r"\w+", re.UNICODE)


def _norm(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _words(value: object) -> list[str]:
    return _WORDS.findall(_norm(value))


def _contains(wanted: list[str], have: list[str]) -> bool:
    size = len(wanted)
    return bool(size) and any(
        have[i : i + size] == wanted for i in range(len(have) - size + 1)
    )


def _strings(element: dict, keys: tuple) -> list[str]:
    return [s for s in (str(element.get(k) or "") for k in keys) if s.strip()]


def _matches(element: dict, keys: tuple, label: str, tier: str) -> bool:
    if tier == TIER_EXACT:
        return any(_norm(s) == label for s in _strings(element, keys))
    wanted = _words(label)
    return any(_contains(wanted, _words(s)) for s in _strings(element, keys))


def _rect(element: dict) -> tuple | None:
    box = element.get("bounds")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        left, top, right, bottom = (int(v) for v in box)
    except (TypeError, ValueError, OverflowError):
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _field_layer(elements: list, label: str, tier: str) -> list:
    return [
        e
        for e in elements
        if e.get("editable") and _matches(e, ("hint", "text", "desc"), label, tier)
    ]


def _distance(label_box: tuple, field_box: tuple, tol: int) -> int | None:
    """Pixels from a label to a field below or right of it, else ``None``."""
    l_left, l_top, l_right, l_bottom = label_box
    f_left, f_top, f_right, f_bottom = field_box
    overlaps_rows = f_top < l_bottom and f_bottom > l_top
    if overlaps_rows and f_left >= l_right - tol:
        return max(0, f_left - l_right)
    overlaps_cols = f_left < l_right and f_right > l_left
    if overlaps_cols and f_top >= l_bottom - tol:
        return max(0, f_top - l_bottom)
    return None


def _claimed_between(
    label_box: tuple, field_box: tuple, others: list, tol: int
) -> bool:
    """True when another text node sits between a label and a field below it --
    the field then belongs to THAT text, not to this label."""
    for box in others:
        inside_rows = box[1] >= label_box[3] - tol and box[3] <= field_box[1] + tol
        inside_cols = box[0] < field_box[2] and box[2] > field_box[0]
        if inside_rows and inside_cols and box != label_box:
            return True
    return False


def _adjacent_layer(elements: list, label: str, tier: str) -> list:
    texts = [
        e for e in elements if not e.get("editable") and _strings(e, ("text", "desc"))
    ]
    labels = [e for e in texts if _matches(e, ("text", "desc"), label, tier)]
    fields = [(e, _rect(e)) for e in elements if e.get("editable")]
    fields = [(e, box) for e, box in fields if box]
    other_boxes = [_rect(e) for e in texts if e not in labels]
    other_boxes = [box for box in other_boxes if box]
    winners: list = []
    for node in labels:
        box = _rect(node)
        if not box:
            continue
        tol = max(4, (box[3] - box[1]) // 4)
        scored = []
        for field, f_box in fields:
            gap = _distance(box, f_box, tol)
            if gap is None:
                continue
            if _claimed_between(box, f_box, other_boxes, tol):
                continue
            scored.append((gap, field))
        if not scored:
            continue
        nearest = min(gap for gap, _ in scored)
        winners.extend(f for gap, f in scored if gap == nearest and f not in winners)
    return winners


_LAYER_FUNCS = ((LAYER_FIELD, _field_layer), (LAYER_ADJACENT, _adjacent_layer))


def resolve_fill(label: object, elements: object) -> dict:
    """``{"element", "layer", "tier", "candidates", "ambiguous"}``. Never raises.

    ``element`` is set only when the first (tier, layer) that matched anything
    matched exactly one field. ``candidates`` is that pair's full list (empty when
    nothing matched anywhere); ``ambiguous`` is true when it held more than one.
    """
    miss = {
        "element": None,
        "layer": "",
        "tier": "",
        "candidates": [],
        "ambiguous": False,
    }
    wanted = _norm(label) if isinstance(label, str) else ""
    if not wanted or not isinstance(elements, (list, tuple)):
        return miss
    usable = [e for e in elements if isinstance(e, dict)]
    # Tier OUTSIDE layer: an exact label anywhere beats a contains hit in an
    # earlier layer, so "Name" never types into a field hinted "Name of your
    # pet" while a visible "Name" label sits above a different field.
    for tier in (TIER_EXACT, TIER_CONTAINS):
        for layer, find in _LAYER_FUNCS:
            found = find(usable, wanted, tier)
            if found:
                return {
                    "element": found[0] if len(found) == 1 else None,
                    "layer": layer,
                    "tier": tier,
                    "candidates": found,
                    "ambiguous": len(found) > 1,
                }
    return miss


def _describe(element: dict) -> str:
    rid = str(element.get("rid") or "")
    name = "id " + rid if rid else str(element.get("cls") or "field")
    box = _rect(element)
    where = " at " + ",".join(str(v) for v in box) if box else ""
    return single_line(name + where, FILL_MAX_CANDIDATE_CHARS)


def refusal_detail(label: object, picked: dict) -> str:
    """Why a fill touched nothing, in terms the model can act on."""
    wanted = single_line(label, FILL_MAX_LABEL_CHARS)
    found = [c for c in (picked.get("candidates") or []) if isinstance(c, dict)]
    if len(found) < 2:
        return "Nothing on this screen is an input field labelled `" + wanted + "`."
    shown = [_describe(c) for c in found[:FILL_MAX_CANDIDATES_LISTED]]
    more = len(found) - len(shown)
    return (
        "`"
        + wanted
        + "` names "
        + str(len(found))
        + " input fields, so nothing was typed: "
        # Resource ids and classes come from the app under test: fenced like
        # every other device string that reaches the model (each one is still
        # single_line'd inside the fence).
        + wrap_untrusted("screen fields", "; ".join(shown))
        + (" (+" + str(more) + " more)" if more > 0 else "")
        + ". Use `type` with a rid or a narrower target to say which one."
    )
