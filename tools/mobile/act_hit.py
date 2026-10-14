"""The two shapes a resolution can take: a hit, or a miss and why."""

from __future__ import annotations

from typing import NamedTuple, Optional

from tools.mobile import perception


# One element's identity WITHIN one dump, for the cross-selector check.
#
# A CONTENT comparison rather than ``is``: a screen that has round-tripped
# through the run store's JSON is the same screen, and object identity would
# call two views of one element a conflict.
#
# **It is the ASSIGNED id, not the content seed, and the difference is a
# confirmed defect.** ``perception.element_seed`` is five observables --
# ``cls``, ``text``, ``desc``, ``rid``, ``bounds`` -- and it omits
# ``clickable``, ``role`` and ``label``. Two elements CAN share all five: the
# clickable wrapper of a chat Send control and the non-clickable child it
# wraps do, which is the very shape ``perception.label_of``'s docstring
# describes on the 2026-09-04 screen. ``label_of`` borrows a label only for a
# TAPPABLE element, so those seed twins get DIFFERENT roles -- and a seed
# intersection then treats them as one element. Measured on such a screen
# before this change: ``{"id": <child>, "role": "send"}`` returned the child,
# ``clickable=False`` and ``role=""``, with ``how="id"`` and
# ``conflict=False`` -- an element the ``role`` selector does not match at
# all. Six of 34 hits over the full selector enumeration were that shape.
#
# ``_assign_ids`` runs inside ``prune``, BEFORE anything resolves, and gives
# a colliding element an ordinal suffix (``eX``, ``eX-2``), so the id is
# unique per dump BY CONSTRUCTION. Intersecting on it makes "a hit means
# every supplied selector agreed" true rather than merely documented; the
# twin pair above now answers ``conflict``, which is what two selectors
# naming different elements has always meant.
#
# Falls back to the seed when an element carries no id, so a hand-built
# screen that never went through ``prune`` still compares by content rather
# than collapsing every element into one empty bucket.
#
# Imported inside the function because ``actions`` must stay importable on
# its own. ``perception`` does not import ``actions``, so there is no cycle.
# (now imported at module level; perception never imports actions)
def _identity(element: object) -> str:
    """One element's identity WITHIN one dump, for the cross-selector check."""
    body = element if isinstance(element, dict) else {}
    assigned = str(body.get("id") or "").strip()
    if assigned:
        return "id:" + assigned
    return "seed:" + perception.element_seed(element)


def _hit(element: dict, how: str, candidates: int) -> dict:
    """A resolution. ``stale`` and ``conflict`` are ALWAYS False here.

    A hit means every selector the target supplied agreed. They were briefly
    settable on a hit, which was dead information AND meant an action could be
    performed on an element only one selector named.
    """
    return {
        "element": element,
        "how": how,
        "candidates": int(candidates),
        "stale": False,
        "conflict": False,
        "supplied": (),
        "stale_selectors": (),
    }


# No element, and WHY -- so the boomerang can say something useful.
# ``candidates`` is non-zero only for an ambiguous ``role``: it is how many
# TAPPABLE controls carried it, and ``executor.missing_element_detail`` turns
# that into "name one of them" rather than "nothing matched".
class Miss(NamedTuple):
    """Why nothing resolved: the optional detail a miss carries."""

    stale: bool = False
    conflict: bool = False
    supplied: tuple = ()
    stale_selectors: tuple = ()
    candidates: int = 0


def _miss(why: Optional[Miss] = None) -> dict:
    """No element, and WHY -- see the comment above."""
    why = why or Miss()
    return {
        "element": None,
        "how": "",
        "candidates": int(why.candidates),
        "stale": bool(why.stale),
        "conflict": bool(why.conflict),
        "supplied": tuple(why.supplied),
        "stale_selectors": tuple(why.stale_selectors),
    }
