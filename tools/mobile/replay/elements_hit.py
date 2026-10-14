"""Replay element resolution: the screen element a tap text or point lands on."""

from __future__ import annotations

from typing import Any

from tools.mobile import (
    action_consts,
)
from tools.mobile import actions as actions_mod
from tools.mobile.replay.elements_geom import _center

#: Ops that really touch the device. ``actions.MUTATING_OPS`` minus ``wait``:
#: a wait forces a re-dump (which is why it is a mutating op there) but it
#: actuates nothing, and counting it as progress charged an escape for a stop
#: that had moved nothing. Derived from that frozenset rather than restated, so
#: an op added there is covered here by construction.
ACTUATING_OPS: frozenset[str] = (
    frozenset(actions_mod.MUTATING_OPS) - actions_mod.WAIT_OPS
)


def _screen_elements(screen: Any) -> list:
    """The element list of a pruned screen, with or without its ``content``
    envelope -- the same two shapes ``actions.candidates_for`` accepts."""
    body = screen if isinstance(screen, dict) else {}
    if isinstance(body.get("content"), dict):
        body = body["content"]
    found = body.get("elements")
    return found if isinstance(found, list) else []


# ``{"element", "candidates", "ambiguous"}`` for one `tap_text`.
#
# Counted by the CONTROL a tap would actuate, not by matching nodes. A Button
# carrying "Send" wrapping a TextView carrying "Send" is two nodes and ONE
# control -- both taps land on the same clickable ancestor -- and counting
# nodes refused every ordinary button as ambiguous (probed, before this).
# :func:`actuated_element` is the one producer of "what does a tap here
# actuate", and the same one the destructive guard is judged by, so the count
# and the guard cannot disagree about a screen.
def resolve_tap_text(text: Any, screen: Any) -> dict:
    """``{"element", "candidates", "ambiguous"}`` for one `tap_text`."""
    target, group = actions_mod.tap_text_candidates(text, screen)
    if target is None:
        return {"element": None, "candidates": 0, "ambiguous": False}
    controls = {}
    for element in group:
        point = _center(element)
        actuated = actuated_element(screen, point) if point else None
        chosen = actuated if isinstance(actuated, dict) else element
        controls[actions_mod._identity(chosen)] = chosen
    if len(controls) > action_consts.TAP_TEXT_MAX_CANDIDATES:
        return {"element": None, "candidates": len(controls), "ambiguous": True}
    resolved = (actions_mod.resolve_target(target, screen) or {}).get("content") or {}
    return {
        "element": resolved.get("element"),
        "candidates": len(controls),
        "ambiguous": False,
    }


def _click_point(point: Any) -> tuple[int, int] | None:
    if not (isinstance(point, (list, tuple)) and len(point) == 2):
        return None
    try:
        return int(point[0]), int(point[1])
    except (TypeError, ValueError, OverflowError):
        return None


def _clickable_box(other: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(other, dict) or not other.get("clickable"):
        return None
    box = other.get("bounds")
    if not (isinstance(box, (list, tuple)) and len(box) == 4):
        return None
    try:
        x1, y1, x2, y2 = (int(v) for v in box)
    except (TypeError, ValueError, OverflowError):
        return None
    return x1, y1, x2, y2


# The element Android delivers a touch at *point* to, as far as a flat
# dump can tell. Never raises.
#
# A touch goes to the DEEPEST clickable view under the finger: a
# non-clickable child does not consume it, so it bubbles to the nearest
# clickable ancestor, and among nested clickables the innermost wins. A
# pruned screen carries bounds but no tree, so "deepest" is approximated by
# the SMALLEST clickable element whose bounds contain the point. Z-order is
# not in the dump either; overlapping siblings are resolved by area, and an
# EQUAL-area tie goes to the LATER element, because uiautomator lists
# siblings in draw order and Android hit-tests the top-most first. A larger
# sheet drawn over a smaller chip still loses to the chip -- a stated limit,
# not a claim.
#
# ``None`` when nothing clickable is under the point -- an EditText in a
# plain layout, say -- in which case the resolved element is all there is to
# judge.
def actuated_element(screen: Any, point: Any) -> dict | None:
    """The element Android delivers a touch at *point* to, as far as a flat dump can tell."""
    xy = _click_point(point)
    if xy is None or not isinstance(screen, dict):
        return None
    x, y = xy
    best: dict | None = None
    best_area = 0
    for other in screen.get("elements") or []:
        box = _clickable_box(other)
        if box is None:
            continue
        x1, y1, x2, y2 = box
        if x < x1 or x > x2 or y < y1 or y > y2:
            continue
        area = max(0, x2 - x1) * max(0, y2 - y1)
        if best is None or area <= best_area:
            best, best_area = other, area
    return best
