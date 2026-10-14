"""Replay element labels: the tokens and text a guard reads off a pruned element."""

from __future__ import annotations

import logging
import re
from typing import Any

from tools.mobile import (
    perception,
)

logger = logging.getLogger("tools.mobile.executor")


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: Any) -> list[str]:
    return _TOKEN_RE.findall(str(text or "").lower())


#: Every string-valued key a pruned element carries. The guard reads ALL of
#: them, and a completeness test asserts this set still matches perception's
#: output -- so adding a field there forces a decision here instead of silently
#: narrowing the evidence. That ratchet is the point: this guard was walked past
#: three times by evidence it did not read.
IDENTIFYING_KEYS: tuple[str, ...] = ("text", "desc", "rid", "cls")


def _split_camel(text: str) -> str:
    """``DeleteAccountButton`` -> ``Delete Account Button``. Delegates.

    The implementation moved to ``perception.split_camel`` when the role
    lexicon needed the same question asked of the same strings: ONE tokenising
    idiom in this package, not three private copies that drift. The name and
    the docstring below are kept because the guard's reasoning lives here.

    Applied to the CLASS name alone. The lexicon matches whole tokens, which is
    what keeps "Deleted items" from being a false positive -- but it also means
    a camel-case run is one token, so ``DeleteAccountButton`` lowercased to
    ``deleteaccountbutton`` never matched ``delete`` and a custom widget class
    walked past the guard. Text and desc are human-written and already
    word-separated, so they are left exactly as the app wrote them.

    Does not widen the lexicon: ``ConfirmationTextView`` becomes
    ``Confirmation Text View`` and ``confirmation`` is still not ``confirm``.
    """
    return perception.split_camel(text)


def _label_part(key: str, value: Any) -> str:
    """One identifying field as guard text: cls split, rid raw AND split."""
    text = str(value or "")
    if key == "cls":
        return _split_camel(text)
    if key == "rid":
        # Raw keeps one-token words (CheckOut -> checkout); split adds signOut -> sign out.
        return perception.with_camel_split(text)
    return text


# Everything about *element* a destructive lexicon should judge.
#
# The guard read ``text`` and ``desc`` only, and four routes walked past it,
# each confirmed by execution:
#
# * the word only in the RESOURCE-ID -- a button showing "Continue" whose id
#   is ``id/delete_account``. For an icon-only control the rid is usually the
#   ONLY evidence there is, which is why dropping it mattered most.
# * the word only in the CLASS name, e.g. a custom ``DeleteAccountButton``.
# * the word only in a CHILD node: a clickable row whose own text is empty,
#   wrapping a label reading "Delete account". That is the ordinary Android
#   list-item shape, not an exotic one.
# * the same element selected by ``id`` rather than ``rid``, which changed
#   the verdict for one control, because the ACTION's text contributed the rid
#   and the element's label did not.
#
# Containment is consulted ONLY for an element with no label of its own. A
# labelled button is judged on its label; a bare wrapper is judged on what it
# visibly contains. Reading descendants unconditionally would let a
# screen-sized container inherit every word on screen and stop ordinary taps.
#
# THE RESIDUAL, stated rather than implied: a control carrying no identifying
# string at all -- empty text, empty desc, no rid, a generic class -- cannot
# be classified by any lexicon, and this does not change that. The guard is a
# strong default, never a proof.
def element_label(element: Any, screen: Any = None) -> str:
    """Everything about *element* a destructive lexicon should judge."""
    if not isinstance(element, dict):
        return ""
    parts = [_label_part(key, element.get(key)) for key in IDENTIFYING_KEYS]
    if element.get("text") or element.get("desc"):
        return " ".join(p for p in parts if p).strip()
    parts.extend(_contained_text(element, screen))
    return " ".join(p for p in parts if p).strip()


# Text of the elements drawn INSIDE *element*.
#
# Only reached for an element with no text or desc of its own. Bounds are the
# only containment signal a pruned screen carries -- the tree shape is gone by
# design -- so "inside" means strictly smaller and fully within, and an
# element is never its own child.
def _contained_text(element: dict, screen: Any) -> list[str]:
    """Text of the elements drawn INSIDE *element*."""
    if not isinstance(screen, dict):
        return []
    outer = _int_box(element.get("bounds"))
    if outer is None:
        return []
    out: list[str] = []
    for other in screen.get("elements") or []:
        if _is_contained_child(element, other, outer):
            for key in ("text", "desc"):
                value = str(other.get(key) or "")
                if value:
                    out.append(value)
    return out


def _int_box(box: Any) -> tuple[int, int, int, int] | None:
    """Four integer bounds, or None when *box* is not a usable rectangle."""
    if not (isinstance(box, (list, tuple)) and len(box) == 4):
        return None
    try:
        x1, y1, x2, y2 = (int(v) for v in box)
    except (TypeError, ValueError, OverflowError):
        return None
    return x1, y1, x2, y2


# True when *other* is drawn inside *outer* and is not *element* itself.
# STRICTLY LARGER is skipped; EQUAL is a child.
#
# This was `>=`, and the reason it was wrong is that it was doing a job
# already done: the two checks above exclude an element from being its
# own child, by identity and by id. What `>=` actually excluded was an
# ordinary `match_parent` x `match_parent` label inside a clickable
# wrapper -- the commonest button shape on Android -- so a transfer
# CTA whose child said "Send money" was judged on
# 'com.bank.app:id/cta Frame Layout' and tapped through unguarded.
# Confirmed live by a review that ran it, and unpinned in BOTH
# directions: flipping the operator left 1697 tests green.
def _is_contained_child(element: dict, other: Any, outer: tuple) -> bool:
    """True when *other* is drawn inside *outer* and is not *element* itself."""
    if not isinstance(other, dict) or other is element:
        return False
    if other.get("id") and other.get("id") == element.get("id"):
        return False
    inner = _int_box(other.get("bounds"))
    if inner is None:
        return False
    x1, y1, x2, y2 = outer
    a1, b1, a2, b2 = inner
    if a1 < x1 or b1 < y1 or a2 > x2 or b2 > y2:
        return False
    return (a2 - a1) * (b2 - b1) <= (x2 - x1) * (y2 - y1)
