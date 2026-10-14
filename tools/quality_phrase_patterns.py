"""Part of the quality-check split (see tools/quality_checks.py): phrase patterns."""

from __future__ import annotations

import re

from tools.quality_phrase_end import _LOCATIVE_OPENERS, _PHRASE_END

# How far a noun phrase may run before _PHRASE_END has to close it. It is LAZY
# and UNBOUNDED, which is a rule; it used to be `{0,4}?`, which was a word window
# nobody had named, measured or tested -- round 7's F2, and a straight
# contradiction of the trap table below, where a word window is rejected for
# _CONTROL_SUFFIX on the grounds that "widening the guess is not the fix".
#
# The window failed in the direction the finding calls primary: a phrase longer
# than five tokens matched NOTHING, so "In the Spending Controls monthly
# transaction cap area, tap Save" -- a step that names a Capitalised container --
# was reported as unfindable. Unbounded, the phrase closes where the sentence
# closes it and the head test decides, which is the same rule the other four
# boundaries now use.
#
# Cost, for ALL THREE scanners rather than this one -- round 9's note, and the
# same omission round 8 made when this paragraph covered _PHRASE_TAIL but not
# _CONTROL_SUFFIX. The three are: the phrase patterns (this tail, run by
# finditer), _CONTROL_SUFFIX (matched per off-app hit and per screen noun), and
# _PHRASE_HERE (matched per screen noun inside an any()). All three are lazy
# repetitions over token classes disjoint from whitespace, so each end position
# has exactly one derivation -- no ambiguity, and `$` in _PHRASE_END guarantees
# termination.
#
# MEASURED, not argued: find_unanchored_first_steps over the 96-case live suite
# takes 2.3 ms end to end. Adversarial single steps -- an "of to" chain of 60, a
# 40-fold repeated screen noun, a 25-fold nominal run, and 200 words with no
# boundary at all -- each cost under 0.5 ms. There is no input in this shape that
# costs a tester anything.
#
# The tail REFUSES to cross a quantity, and _PHRASE_HERE (the screen path) is
# allowed to. That asymmetry is the most expensive line in this file: round 9
# challenged it and proposed removing it, and revision 10 tried THREE ways to
# remove it. All three are recorded, with their measurements, because the next
# reader will otherwise retry one:
#
#   (a) let _PHRASE_END CLOSE on a quantity (revision 9's attempt) -- broke 24 of
#       this file's tests: "on debit 4521" closed as "debit", so a card number
#       read as a place.
#   (b) cross a quantity everywhere, on round 9's argument that _is_a_head
#       refuses a number so one can never BE the head. MEASURED AND REFUTED:
#       trimming the number hands the head to the token before it, and "debit"
#       is in no not-a-place set. Live clear-14 fell to 7/14, partition to
#       11/79/6.
#   (c) cross a quantity only when a word follows it. Also refuted, and more
#       interestingly: TC-021 ("...on debit 4521 WHILE capturing network
#       traffic") crossed, closed at "while", trimmed back to "debit" and went
#       silent -- so (c) is (b) with extra steps. It also cost the screen path
#       four anchors ("From Card controls debit 4521, enter '1000' in monthly
#       cap") because a quantity before a COMMA is not crossable, which is
#       exactly what _PHRASE_HERE needs to cross. Partition 31/52/13, 13/14.
#
# So the asymmetry stands, and the reason is now stated rather than assumed: the
# three place patterns START AT A PREPOSITION and guess where the phrase is, so a
# number is the only signal that the guess has left the noun phrase; _PHRASE_HERE
# starts at a word already known to be a screen noun, so it can cross a number
# and still answer on a real head. The disclosed residual is in the plan's
# "Known false positives" and pinned in op 5.
#
# The PARTICIPLE stop is likewise refused only in HEAD position, by _is_a_head:
# stopping the tail at one would make "In the cards PENDING review list, tap the
# first card" match nothing, which is the same false positive as the residual
# above.
#
# The PARTICIPLE stop is still not shared with _CONTROL_SUFFIX, and that stays a
# decision with a reason: the two read in opposite directions. _CONTROL_SUFFIX
# runs LEFT to RIGHT for the noun that ENDS a compound, so stopping early keeps a
# step exempt; this bounds a phrase whose HEAD decides, so stopping early would
# match nothing at all -- and "In the cards PENDING review list, tap the first
# card" would become the same false positive as the 2024 case above. A participle
# is refused only in HEAD position, by _is_a_head, and that string is pinned.
_PHRASE_TAIL = r"(?:\s+(?![\w-]*\d)[A-Za-z'\u2019>/-]+)*?"

# The opener carries the same quantity guard as the tail and as _FROM_PLACE,
# which is the THIRTEENTH member of the recurring class and was found by asking
# the enumeration question of revision 8's own fix: _FROM_PLACE opened with
# [A-Za-z] and these two opened with [A-Za-z0-9], so one predicate refused a
# phrase beginning in a quantity and two accepted it. Live TC-032 ("At 00:05
# Asia/Riyadh ON 1ST OF MONTH query backend monthly spend API...") anchored on a
# DATE for exactly that reason -- silent either way, since the case is off-app,
# but silent for a reason that would not survive being read aloud.
_PHRASE_START = r"(?![\w-]*\d)[A-Za-z'\u2019>/-]+"

_LOCATIVE_PHRASE = re.compile(
    r"\b(?:" + _LOCATIVE_OPENERS + r")\s+(?:the\s+|an?\s+|your\s+|its\s+)?"
    r"(" + _PHRASE_START + _PHRASE_TAIL + r")" + _PHRASE_END,
    re.IGNORECASE,
)

# "From <place>" anchors -- but "from SAR 3,000 to SAR 5,000" introduces a
# VALUE, not a location. Measured: without this guard it falsely anchored two of
# the clear weak cases. Currency-agnostic: an allow-list of SAR/USD/EUR let
# "from GBP 3,000 to GBP 5,000" through as a place.
_FROM_PLACE = re.compile(
    r"\bfrom\s+(?![A-Za-z]{3}\s*[\d.,])(?!\d)(?:the\s+)?"
    r"(" + _PHRASE_START + _PHRASE_TAIL + r")" + _PHRASE_END,
    re.IGNORECASE,
)
