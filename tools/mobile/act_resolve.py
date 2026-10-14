"""Resolve a target to one element of the pruned screen, or say why none."""

from __future__ import annotations

import logging

from tools.mobile.act_cands import candidates_for
from tools.mobile.act_hit import Miss, _hit, _identity, _miss
from tools.mobile.act_select import OURS, SELECTORS

logger = logging.getLogger("tools.mobile.actions")


# Find *target* on the pruned screen. A miss is content, not an error.
#
# ``{"error": None, "content": {"element", "how", "candidates", "stale",
# "conflict", "supplied", "stale_selectors"}}``. ``how`` is ``""`` on a miss
# so a caller can branch on a constant rather than on the absence of a key.
#
# **An element is returned only when it is among the matches of EVERY
# selector the target supplied.** Preference order (:data:`SELECTORS`)
# decides which element is acted on among those that agree, and which
# selector ``how`` names; it never decides whether the others are consulted.
# Outcomes:
#
# * every supplied selector matched and they agree -> a HIT. The narrowest
#   selector picks the element, and a CLICKABLE candidate is preferred, so a
#   role resolves the tappable wrapper rather than the child that carries the
#   word.
# * a ``role`` that still matches more than one TAPPABLE control after every
#   other selector has narrowed it -> a miss carrying that count. Ambiguity is
#   not a coin toss: picking the first in dump order is how "Send voice
#   message" was tapped instead of Send. Checked AFTER the intersection so a
#   target carrying its own tie-break -- ``{"role": "input", "label":
#   "Message"}`` -- is still answered.
# * a supplied selector matched NOTHING while another matched something ->
#   ``stale``: the plan was built on a screen that no longer exists.
# * two supplied selectors both matched, but no element satisfies both ->
#   ``conflict``.
# * nothing matched at all -> ``stale`` when one of :data:`OURS` was supplied
#   and missed, otherwise a plain miss. ``case_runner`` charges those two
#   differently.
def resolve_target(target: object, pruned: object) -> dict:
    """Find *target* on the pruned screen; a miss is content, not an error."""
    try:
        found = candidates_for(target, pruned)
        if not found:
            return {"error": None, "content": _miss()}
        return {"error": None, "content": _resolve_found(found)}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.actions.resolve_target failed")
        return {"error": str(exc), "content": None}


def _resolve_found(found: dict) -> dict:
    """The ``content`` for a target whose selectors produced *found* candidates."""
    supplied = tuple(name for name in SELECTORS if name in found)
    matched = {name: group for name, (_how, group) in found.items() if group}
    missed = tuple(name for name in supplied if name not in matched)
    if not matched:
        ours_missed = tuple(name for name in missed if name in OURS)
        return _miss(
            Miss(stale=bool(ours_missed), supplied=supplied, stale_selectors=missed)
        )
    if missed:
        return _miss(Miss(stale=True, supplied=supplied, stale_selectors=missed))
    return _pick_agreed(found, matched, supplied)


def _selector_order() -> dict:
    """Each selector name mapped to its preference index."""
    return {name: index for index, name in enumerate(SELECTORS)}


def _agreed_ids(matched: dict) -> set:
    """The assigned ids of the elements every matched selector agrees on."""
    # Elements satisfying EVERY supplied selector. Compared by the ASSIGNED
    # id (see `_identity`): the content seed omits `clickable`, so a
    # clickable wrapper and its non-clickable child are one bucket to it,
    # and a target could be answered by an element only ONE of its
    # selectors matched.
    agreed_ids: set | None = None
    for group in matched.values():
        group_ids = {_identity(element) for element in group}
        agreed_ids = group_ids if agreed_ids is None else (agreed_ids & group_ids)
    return agreed_ids or set()


def _survivors(matched: dict, agreed_ids: set) -> tuple[list, list]:
    """The narrowest selector's agreed elements, and the clickable ones among them."""
    order = _selector_order()
    narrowest = min(matched, key=lambda name: (len(matched[name]), order[name]))
    agreed = [e for e in matched[narrowest] if _identity(e) in agreed_ids]
    # THEIR clickable preference: the tap target is the wrapper, and the
    # element carrying the word is often its non-clickable child.
    tappable = [e for e in agreed if e.get("clickable")]
    return agreed, tappable


def _pick_agreed(found: dict, matched: dict, supplied: tuple) -> dict:
    """Intersect the selectors' matches and choose the element, or say why none."""
    agreed_ids = _agreed_ids(matched)
    if not agreed_ids:
        return _miss(Miss(conflict=True, supplied=supplied))
    agreed, tappable = _survivors(matched, agreed_ids)
    # THEIR ambiguity rule, applied to what SURVIVED the cross-check rather
    # than to the role's own matches -- which is their own "ambiguity falls
    # through" intent, generalised: any other selector may narrow it, and
    # only a role still ambiguous at the end is a miss.
    if "role" in matched and len(tappable) > 1:
        return _miss(Miss(supplied=supplied, candidates=len(tappable)))
    chosen = (tappable or agreed)[0]
    preferred = min(matched, key=lambda name: _selector_order()[name])
    return _hit(chosen, found[preferred][0], len(matched[preferred]))
