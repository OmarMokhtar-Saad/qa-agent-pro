"""The element budget: inside-the-panel elements first, the rest in the headroom."""

from __future__ import annotations

from tools.mobile.perception_limits import MAX_ELEMENTS, MAX_PACKET_HEADROOM


def _intersects(box: list, selected: tuple) -> bool:
    return len(box) == 4 and not (
        box[0] >= selected[2]
        or box[1] >= selected[3]
        or box[2] <= selected[0]
        or box[3] <= selected[1]
    )


# Classified ONCE, into a list, because the same visibility test written
# twice is two conditions that drift apart.
def _classify_by_panel(
    elements: list[dict], selected: tuple
) -> list[tuple[dict, bool]]:
    """Pair each element with whether its box intersects the panel."""
    return [
        (element, _intersects(element.get("bounds") or [], selected))
        for element in elements
    ]


# No panel, so no classification to make: one budget, document
# order, and the drops are counted under their own name. Attributing
# them to the inside class would be inventing a classification from
# a rectangle we do not have, which is the mistake `prune`'s
# whole history is made of.
# DOCUMENT ORDER, and the independence is ONE-DIRECTIONAL: an
# inside decision reads only the inside counter and its own cap,
# so nothing the outside class does can evict an inside element.
# The reverse is not true and does not need to be -- the outside
# budget is a function of `inside_count` -- and the clamp above
# is what stops that dependence turning back into competition.
def _budgeted(
    classified: list[tuple[dict, bool]], budget: dict[bool, int]
) -> tuple[list[dict], dict[bool, int]]:
    """Keep each side's elements up to its own budget, in document order."""
    used = {True: 0, False: 0}
    kept: list[dict] = []
    for element, visible in classified:
        if used[visible] < budget[visible]:
            used[visible] += 1
            kept.append(element)
    return kept, used


# THE TOTAL IS BOUNDED; THE OUTSIDE CLASS IS NOT CAPPED.
#
# Outside elements get whatever the total leaves after the inside
# class has taken its own cap. Capping the outside class instead
# deleted content from dumps that FIT -- 110 elements on a tablet
# under a stale panel lost 25 of the tester's controls, where
# today's code carries all 110 -- and it deleted them from the one
# class whose membership we have already decided we cannot trust.
#
# `min(inside_count, MAX_ELEMENTS)` IS THE INVARIANT (in `_cap_elements`).
# Delete the clamp and the outside budget keeps growing with the inside
# class, so inside overflow starts eating the outside budget and the two
# classes are competing again -- which is the defect all four ordering rules
# died of. With the clamp, the budget is pinned at the headroom once
# the inside class is full, so inside overflow adds NOTHING to
# outside loss.
def _cap_elements(
    elements: list[dict], selected: tuple | None
) -> tuple[list[dict], int, int, int]:
    """Apply the element budget: ``(kept, dropped_inside, dropped_outside, dropped_unclassified)``."""
    if selected is None:
        return elements[:MAX_ELEMENTS], 0, 0, max(0, len(elements) - MAX_ELEMENTS)
    classified = _classify_by_panel(elements, selected)
    inside = sum(1 for _, visible in classified if visible)
    outside_room = MAX_ELEMENTS + MAX_PACKET_HEADROOM - min(inside, MAX_ELEMENTS)
    budget = {True: MAX_ELEMENTS, False: outside_room}
    kept, used = _budgeted(classified, budget)
    return kept, inside - used[True], len(classified) - inside - used[False], 0
