"""Part of the quality-check split (see tools/quality_checks.py): screens."""

from __future__ import annotations

import re
from typing import cast

from tools.quality_controls import _PHRASE_HERE, _is_modifier_of_a_control
from tools.quality_heads import _any_phrase_is_a_place, _head_is_a_place
from tools.quality_nav_patterns import _NAV_TO_NAMED, _URL_DESTINATION
from tools.quality_nouns import _SCREEN_NOUN, _SCREEN_READER
from tools.quality_off_app import (
    _AUTH_BOUNDARY,
    _OFF_APP_FIRST_STEP,
    _OFF_APP_MESSAGE,
    _OFF_APP_TRANSACTION,
)
from tools.quality_phrase_patterns import _FROM_PLACE, _LOCATIVE_PHRASE


# A nav verb is necessary and was, until revision 8, sufficient -- which left
# the verb deciding a question about the URL's ROLE (round 7's F6). A URL the
# step then puts into a control is data: "Open https://evil... IN THE REDIRECT
# LINK FIELD" is the security case whose whole point is the field, and it must
# read the same as the "Type ..." twin already pinned beside it.
#
# The test is _head_is_a_place on what follows, so the URL path finally reaches
# the same shared head rule as every other place predicate -- it was the last
# one that did not.
def _url_names_a_destination(text: str) -> bool:
    """True when a URL in `text` is somewhere the tester GOES, not data."""
    for match in _URL_DESTINATION.finditer(text):
        rest = text[match.end() :]
        # ONE scan of the remainder, not two. Revision 8 called .search() and
        # then _any_phrase_is_a_place(), which ran finditer over the same text a
        # second time -- round 8's note, and the only place the nested-scan cost
        # was real.
        phrases = [(m.group(1) or "").strip() for m in _LOCATIVE_PHRASE.finditer(rest)]
        if phrases and not any(_head_is_a_place(p) for p in phrases):
            continue
        return True
    return False


# This is the FOURTEENTH member of this plan's recurring class and the one it
# kept coming back to: _names_a_screen was the last truthy source of "place"
# that never reached _head_is_a_place. It was guarded only by
# _is_modifier_of_a_control, and round 8 showed that guard failing OPEN in
# exactly the cases revision 8 had just taught it to refuse -- _CONTROL_SUFFIX
# stops at a participle and at a quantity, so "Enter 2500 in the menu SPENDING
# limit field" and "Enter 5000 in the toolbar 2 field" cleared the modifier
# test with zero iterations and the screen noun was promoted to a screen. The
# step is a FIELD; its pinned twin "Toggle the parental controls switch OFF"
# was flagged. One rule answering in two directions, again.
#
# Both guards are kept rather than one replacing the other, because they ask
# different questions: the modifier test reads FORWARD for the noun that ends a
# compound, this reads the phrase the screen noun HEADS. A screen noun counts
# only if it survives both.
def _screen_noun_heads_a_place(probe: str, start: int) -> bool:
    """True when the phrase beginning at `start` has a place for its head."""
    match = _PHRASE_HERE.match(probe, start)
    return bool(match) and _head_is_a_place(
        cast("re.Match[str]", match).group(1).strip()
    )


# A screen noun counts only where it is not MODIFYING a control: "the Cards
# tab" is a place, "the menu icon" is a button. Every occurrence is tried,
# because one qualified mention must not hide an unqualified one.
#
# A URL counts only where a navigation verb takes it, or where it opens the
# step, AND the step does not then put it into a control -- see
# _url_names_a_destination. All of those are the same rule: a keyword is not a
# location just because it appears.
def _names_a_screen(text: str) -> bool:
    """True when the text names a screen outright (a screen noun, or a URL)."""
    if not text:
        return False
    if _url_names_a_destination(text):
        return True
    probe = _SCREEN_READER.sub(" ", text)
    return any(
        not _is_modifier_of_a_control(probe[m.end() :])
        and _screen_noun_heads_a_place(probe, m.start())
        for m in _SCREEN_NOUN.finditer(probe)
    )


def _names_a_place(text: str) -> bool:
    """True when the text names a place the tester can physically be.

    For a STEP only. Preconditions get _names_a_screen instead: they are free
    prose dense with prepositions, and the locative rule reads "logged in with
    push enabled" or "test at month start Asia/Riyadh" as locations. Measured on
    the live run, the permissive form anchored 4 cases on preconditions and not
    one of them named a screen.
    """
    if not text:
        return False
    if _names_a_screen(text):
        return True
    return (
        _any_phrase_is_a_place(_NAV_TO_NAMED, text)
        or _any_phrase_is_a_place(_LOCATIVE_PHRASE, text)
        or _any_phrase_is_a_place(_FROM_PLACE, text)
    )


def _off_app_match(action: str):
    """The first off-app match that is not the MODIFIER of an in-app control."""
    for pattern in (
        _OFF_APP_FIRST_STEP,
        _OFF_APP_TRANSACTION,
        _OFF_APP_MESSAGE,
        _AUTH_BOUNDARY,
    ):
        for match in pattern.finditer(action):
            if _is_modifier_of_a_control(action[match.end() :]):
                continue
            return match
    return None


def _first_step_is_off_app(action: str) -> bool:
    """True when the step is not app navigation and so has no screen to name."""
    return _off_app_match(action) is not None
