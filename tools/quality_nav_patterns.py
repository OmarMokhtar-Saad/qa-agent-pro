"""Part of the quality-check split (see tools/quality_checks.py): nav patterns."""

from __future__ import annotations

import re

from tools.quality_phrase_end import _LOCATIVE_OPENERS, _PHRASE_END
from tools.quality_phrase_patterns import _PHRASE_START, _PHRASE_TAIL

# A navigation verb anchors only when its destination is a PLACE. "Open the
# per-transaction limit field" is a field, and treating any "Open ..." as
# sufficient is the mistake the prompt itself calls out ("open any upload
# section").
#
# The capture runs to the end of the noun phrase rather than to the first token,
# because the head is what decides and it is often preceded by the name: in
# "Open the 'Per-transaction limit' field" the quoted part is the field's NAME
# and "field" is the head. Stopping early anchored every quoted control in the
# suite -- round 6's largest leak.
#
# It used to require a quoted or Capitalised destination, and this pattern was
# the one case-SENSITIVE regex in the detector because of it. That requirement
# was a PROXY for "is this a place or a control", written before a direct test
# existed; _head_is_a_place is that direct test, so the proxy is gone. Measured:
# dropping it changes nothing on the live suite (23/58/15, 14/14) and nothing on
# any weak fixture, and it closes the case split round 6 reported -- "in the
# cards list" was silent while "navigate to cards" was weak, purely because the
# locative patterns were IGNORECASE and this one was not.
_NAV_VERBS = r"navigate to|go to|open|launch|return to|back to"

_NAV_TO_NAMED = re.compile(
    r"\b(?:" + _NAV_VERBS + r")\s+(?:the\s+)?"
    r"((?:'[^']+'|\"[^\"]+\"|"
    + _PHRASE_START
    + r")"
    + _PHRASE_TAIL
    + r")"
    + _PHRASE_END,
    re.IGNORECASE,
)

# On a web suite the URL IS the location -- but only when the tester GOES there.
# A URL the tester TYPES is data: "Enter https://... in the URL field and tap Go"
# opens inside a field like any other clear-14 case, and "Type https://evil... in
# the redirect link field" is a security case whose whole point is the field.
#
# Round 7 found this the same way round 6 found _names_a_screen: by enumerating
# every truthy source in _names_a_place and asking which ones skip the shared
# tests. A bare https?:// scan was the last one -- the FIFTH place path, and the
# fourth appearance of "a keyword short-circuits ahead of the head test".
#
# So the URL must be taken by a NAVIGATION verb, or stand at the head of the step
# as its starting point. _NAV_VERBS is shared with _NAV_TO_NAMED above rather
# than restated, because a nav-verb list in two places is this plan's oldest
# recurring bug in miniature.
#
# Revision 8 also consumes the URL TOKEN itself, so the caller can read what the
# step does with it. Round 7's F6: requiring a nav verb left the verdict resting
# on the verb ALONE -- "Type https://evil... in the redirect link field" was weak
# and "Open https://evil... in the redirect link field" anchored, though the URL
# plays the same role in both. A destination the step immediately puts INTO a
# control is data, whatever verb introduced it.
#
# A LOCATIVE preposition opens one too ("In https://app.example.com/cards, tap
# Freeze"), which round 8 found reported as unfindable: it matched no nav verb,
# did not open the step, and no head test could rescue it because _is_a_head
# refuses a URL by design. That was a false POSITIVE with no disclosure, which is
# the one direction this advisory check must not fail in silently.
_URL_DESTINATION = re.compile(
    r"(?:\b(?:" + _NAV_VERBS + r")\s+(?:the\s+)?"
    r"|\b(?:" + _LOCATIVE_OPENERS + r"|from)\s+"
    r"|^\s*)https?://[^\s,;]*",
    re.IGNORECASE,
)
