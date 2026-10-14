"""Part of the quality-check split (see tools/quality_checks.py): controls."""

from __future__ import annotations

import re

from tools.quality_nouns import _KNOWN_NOUNS
from tools.quality_phrase_end import _NP_BREAK, _PHRASE_END
from tools.quality_phrase_patterns import _PHRASE_START

# A word is the MODIFIER of an in-app control when the compound noun it starts
# ends in a control noun: "the ATM withdrawal LIMIT", "the merchant site FIELD",
# "the ATM locator ICON", "the menu ICON", "the toolbar FIELD". Both the off-app
# keywords and the screen nouns need exactly this test, so there is exactly one
# of it -- see the caller list in its docstring.
#
# WHERE THE COMPOUND ENDS is the whole difficulty, and rounds 5 and 6 each broke
# a different answer to it:
#
#   {0,2} words (rev 5)  missed "payment gateway automatic retry attempt COUNTER"
#   {0,5} words          cancelled the genuine exemption in "ATM withdrawal SAR
#                        500 exceeding the daily limit" -- a wide window reaches
#                        into the next clause
#   a stop-word list     (rev 6) was wrong in BOTH directions: it ended the
#                        compound at "of"/"to", so "merchant site terms OF
#                        service checkbox" stayed exempt, and it did NOT end at
#                        "exceeding", so "ATM withdrawal SAR 500 exceeding daily
#                        limit" lost its exemption as soon as the article was
#                        dropped. Every fixture that scored 5/5 for it happened
#                        to contain an article -- an artifact, not a measurement.
#
# So the rule is inverted, per round 6's suggestion: a compound CONTINUES across
# nominal material and stops at the three things that end a noun phrase --
# punctuation, a QUANTITY (any token carrying a digit), and a new clause opened
# by a function word or a PARTICIPLE. "of" and "to" are the two function words
# that build genuine compounds ("terms of service", "time to live", "point of
# sale"), so they are the two allowed through.
#
# RESIDUAL, stated rather than tuned away: a participle can also be a legitimate
# noun modifier ("the merchant site SPENDING limit field"), so that compound
# stops early and the step stays exempt. Both remaining inexactnesses in this
# boundary resolve toward SILENCE, which is the direction this advisory check
# chooses everywhere else -- a false positive spends a tester's attention, a
# false negative costs them nothing they had before.
#
# _NP_BREAK is shared with _PHRASE_END on purpose; see its comment.
#
# The compound ends at any noun this detector does not treat as a place -- the
# same _KNOWN_NOUNS _head_is_a_place tests, not the _CONTROL_NOUNS subset it used
# to name. That subset was round 7's F3, the TWELFTH member of the recurring
# class: "in the monthly cap section" was weak (the locative path applied the
# generic-container rule) while "in the menu section" anchored, because "section"
# was not a control noun, so _is_modifier_of_a_control said no and _names_a_screen
# short-circuited on "menu" before the head test could ever run. One policy --
# "which nouns are not places" -- was answered by two sets, which is the only bug
# this plan has ever had.
# The repetition below used to carry a second alternative, `|(?:of|to)\s+`, to
# let a compound cross the two words that build one. It was REDUNDANT and
# DANGEROUS, and revision 9 deleted it. Redundant because "of" and "to" are
# deliberately absent from _NP_BREAK, so the first branch already accepts them --
# they are not break words, carry no digit and do not end in "ing". Dangerous
# because two branches that match the same input make the group the classic
# ambiguous `(a|a)*?`: a step carrying a long chain of "of"/"to" with no
# terminating noun makes the FAILURE path explore 2**k derivations. This function
# runs on every finalize over model-produced text, and the try/except in
# find_unanchored_first_steps catches exceptions -- it cannot catch a hang.
# Round 8 found it; the fix is a deletion, and the behaviour is identical.
# Round 8 deleted a second, redundant alternative from the repetition below
# because two branches matching the same input made it the ambiguous
# `(a|a)*?`. The 2026-09-02 audit (F11) found the SAME class one level up, in
# the two quantifiers round 8 left unbounded. `^[\s'\u2019"-]*` accepts both a
# space and a hyphen, so the tail `"- " * 4000` let the leading class start at
# any of 8,000 offsets and made the lazy word repetition re-derive from each:
# 9.0 s measured through find_unanchored_first_steps, x4 per doubling, on every
# finalize, over model-produced text. That function's try/except catches an
# exception; it cannot catch a hang.
#
# So both quantifiers are BOUNDED. A compound noun that ends in a control noun
# is a handful of words ("the merchant site terms of service checkbox" is six),
# and no real step opens with a dozen quote/hyphen characters, so nothing that
# used to match stops matching -- and a compound longer than the bound resolves
# toward SILENCE about the compound, which flags the step, the direction this
# advisory check already chooses everywhere else.
_CONTROL_SUFFIX = re.compile(
    r"^[\s'\u2019\"-]{0,12}"
    # ... across nominal words: not a break word, not a participle, no digit
    r"(?:(?!(?:" + _NP_BREAK + r")\b)(?![\w-]*\d)(?![\w-]*ing\b)"
    r"[\w\u2019'-]+\s+){0,12}?"
    r"(?:" + "|".join(sorted(_KNOWN_NOUNS)) + r")\b",
    re.IGNORECASE,
)

# The input bound that sits alongside the bounded pattern above: twelve words
# plus their separators cannot exceed this, so a control noun that the pattern
# could still reach is never outside the window, and one that is outside it was
# already beyond the repetition's reach.
_MAX_CONTROL_TAIL_CHARS = 300

# The phrase that STARTS at a given offset, using the same opener, tail and
# boundary as the three place patterns. _names_a_screen needs it: a screen noun
# is only a screen when the phrase it heads is a place, and until revision 9 that
# path answered without ever consulting _head_is_a_place.
#
# Its tail is the ONE that may cross a quantity -- see the three refuted attempts
# in _PHRASE_TAIL's comment above for why that is a measurement rather than a
# preference. "Enter 5000 in the toolbar 2 FIELD" has to reach "field" to be
# judged, and "From Card controls debit 4521, ..." has to reach past a card
# number to keep its anchor.
_PHRASE_HERE = re.compile(
    r"(" + _PHRASE_START + r"(?:\s+[A-Za-z0-9'\u2019>/-]+)*?)" + _PHRASE_END,
    re.IGNORECASE,
)


def _is_modifier_of_a_control(tail: str) -> bool:
    """True when `tail` continues a compound noun that ends in a control noun.

    Callers -- and this list is the claim, so it is enumerated rather than
    counted from memory:
      1. _off_app_match, so "the merchant site FIELD" is not a merchant visit
      2. _names_a_screen, so "the menu ICON" is not the menu

    Round 6 found (2) missing: _names_a_screen was a bare _SCREEN_NOUN substring
    scan sitting AHEAD of every other predicate, so "Tap the menu icon", "Enter
    5000 in the toolbar field" and "Toggle the footer switch OFF" all anchored on
    a word that was modifying a control -- structurally the same bare-keyword
    class this detector eradicated for "terminal" in round 3.
    """
    return bool(_CONTROL_SUFFIX.match(tail[:_MAX_CONTROL_TAIL_CHARS]))
