"""Expected-result patterns for unfalsifiable (nondeterministic) oracles."""

from __future__ import annotations

import re

# Expected-result phrasing that accepts MORE THAN ONE outcome. Each pattern is
# anchored on an explicit disjunction or an explicit "give up and record it"
# escape -- not on the mere presence of "or", which appears legitimately in
# "the Cancel order option is hidden or disabled" (one outcome, two renderings).
NONDETERMINISTIC_PATTERNS: list[re.Pattern] = [
    # "Either X or Y" spanning a bounded window.
    re.compile(r"\beither\b[^.]{0,160}?\bor\b", re.IGNORECASE),
    # "..., or if <condition> <second OUTCOME>" — a genuinely alternative result.
    # The outcome noun is required: "an error is shown if the field is empty or if
    # it exceeds 50 characters" is ONE outcome with two conditions, and matching a
    # bare "or if" reported every such validation case as unfalsifiable.
    re.compile(
        r"\bor\s+if\b[^.]{0,120}?\b(?:message|validation|error|screen|dialog|"
        r"banner|toast|success|sheet|state|status|popup|alert|warning|result)\b",
        re.IGNORECASE,
    ),
    # "note the exception and stop" / "note exception and stop"
    re.compile(r"\bnote\b[^.]{0,60}?\bexception\b[^.]{0,40}?\bstop\b", re.IGNORECASE),
    # "record as blocked" / "record it as blocked"
    re.compile(r"\brecord\b[^.]{0,30}?\bas\s+blocked\b", re.IGNORECASE),
    # "if <no channel/not instrumented> ... record it as ..." — self-voiding.
    # record/note must be a VERB: "the note field is unchanged" is a perfectly
    # deterministic assertion and was being flagged on the noun.
    re.compile(
        r"\bif\s+no\b[^.]{0,90}?\b(?:record|note)\s+"
        r"(?:it|this|that|as|the\s+actual|which|whichever)\b",
        re.IGNORECASE,
    ),
    # "X is accepted, or a lower cap is enforced" — an outcome pair on a
    # boundary probe, where the case declares both results acceptable.
    re.compile(
        r"\b(?:accepts?|accepted)\b[^.]{0,80}?\bor\b[^.]{0,60}?"
        r"\b(?:enforces?|truncat\w+|blocks?|rejects?|caps?)\b",
        re.IGNORECASE,
    ),
    # "record which behaviour occurred" — an observation, not an assertion.
    re.compile(
        r"\brecord\b[^.]{0,40}?\bwhich\b[^.]{0,40}?\b(?:behaviou?r|occurred|happened)\b",
        re.IGNORECASE,
    ),
]

# The words this module treats as opening a negative scope. ONE list, used by
# both the negated-disjunction guard and the display-verb lookahead below --
# they were written separately and immediately disagreed ("shows not applicable
# text" and "displays neither a message nor a hint" slipped through a lookahead
# that knew only "no/none/nothing").
_NEGATION_WORDS = r"(?:no|not|without|never|neither|none|nothing)"

# The bilingual whitelist, as a LOOKAHEAD rather than a strip. This project
# mandates bilingual cases, so "the button is disabled or displays the Arabic
# equivalent tooltip" is ONE outcome in two locales and must not be reported.
#
# The obvious way to do that is to DELETE the locale disjunct before the rules
# run. That was tried for five review rounds and abandoned: a strip that eats
# text can eat the wrong text, and every attempt to bound how far it reached --
# by punctuation, by word count, by a list of conjunctions -- was defeated by a
# sentence one word to the side, three times destroying a detection the module
# already SHIPPED ("Either it freezes or the limit saves and the AR equivalent
# toast appears" went silent). A whitelist must be able to suppress a rule; it
# must never be able to delete a rule's anchor. So nothing is deleted: each new
# rule below carries its own lookahead, and the seven shipped rules keep the
# semantics they shipped with, byte for byte.
#
# The lookahead reaches to the end of the CLAUSE (no character budget, no
# conjunction list -- those are what kept failing). Its cost is a miss, not a
# lost detection: a genuine disjunction that shares a clause with a bilingual
# adjunct is not reported. Pinned in both directions.
_LOCALE_RENDERING = (
    r"(?:\b(?:AR|EN|Arabic|English)\s+equivalent\b"
    r"|\bequivalent\s+(?:AR|EN|Arabic|English|localiz\w+|localis\w+|translat\w+)\b)"
)

_NONDETERMINISTIC_PATTERNS = NONDETERMINISTIC_PATTERNS  # legacy name (WX removes it)
