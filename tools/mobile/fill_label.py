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
3. ``contained`` -- an EDITABLE node whose bounds hold the centre of a
   matching text node (a Material floating label drawn inside its field).
4. ``adjacent`` -- the nearest EDITABLE node below, or to the right of, a
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

from typing import Any

from tools.mobile.fill_label_geom import _rect  # noqa: F401
from tools.mobile.fill_label_layers import (  # noqa: F401
    LAYER_ADJACENT,
    LAYER_CONTAINED,
    LAYER_FIELD,
    LAYERS,
    _adjacent_layer,
    _field_layer,
)
from tools.mobile.fill_label_match import (  # noqa: F401
    TIER_CONTAINS,
    TIER_EXACT,
    _norm,
)
from tools.mobile.fill_label_owner import contained_layer
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

_LAYER_FUNCS = (
    (LAYER_FIELD, _field_layer),
    (LAYER_CONTAINED, contained_layer),
    (LAYER_ADJACENT, _adjacent_layer),
)


def _miss_result() -> dict:
    miss: dict[str, Any] = {
        "element": None,
        "layer": "",
        "tier": "",
        "candidates": [],
        "ambiguous": False,
    }
    return miss


def _search(usable: list, wanted: str) -> dict | None:
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
    return None


def resolve_fill(label: object, elements: object) -> dict:
    """``{"element", "layer", "tier", "candidates", "ambiguous"}``. Never raises.

    ``element`` is set only when the first (tier, layer) that matched anything
    matched exactly one field. ``candidates`` is that pair's full list (empty when
    nothing matched anywhere); ``ambiguous`` is true when it held more than one.
    """
    wanted = _norm(label) if isinstance(label, str) else ""
    if not wanted or not isinstance(elements, (list, tuple)):
        return _miss_result()
    usable = [e for e in elements if isinstance(e, dict)]
    return _search(usable, wanted) or _miss_result()


def _describe(element: dict) -> str:
    rid = str(element.get("rid") or "")
    name = "id " + rid if rid else str(element.get("cls") or "field")
    box = _rect(element)
    where = " at " + ",".join(str(v) for v in box) if box else ""
    return single_line(name + where, FILL_MAX_CANDIDATE_CHARS)


def _many_text(wanted: str, found: list) -> str:
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


def refusal_detail(label: object, picked: dict) -> str:
    """Why a fill touched nothing, in terms the model can act on."""
    wanted = single_line(label, FILL_MAX_LABEL_CHARS)
    found = [c for c in (picked.get("candidates") or []) if isinstance(c, dict)]
    if len(found) < 2:
        return "Nothing on this screen is an input field labelled `" + wanted + "`."
    return _many_text(wanted, found)
