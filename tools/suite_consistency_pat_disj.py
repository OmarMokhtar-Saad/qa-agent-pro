"""Disjunction, escape-hatch and negation patterns for unfalsifiable oracles."""

from __future__ import annotations

import re

from tools.suite_consistency_pat_oracle import _LOCALE_RENDERING, _NEGATION_WORDS

# Rules added 2026-08-16 for two of the expected-result shapes the list above
# missed on a real 96-case suite. They live in their OWN list because they are
# evaluated differently: only these are subject to the negated-disjunction
# guard below. The SEVEN rules above are NOT run through that guard, because it
# begins at a negation and one of them ("if no <channel> ... record it as
# blocked") is ANCHORED on a negation -- guarding it silently deleted that
# detection as soon as the channel list contained an "or". A guard meant to
# remove false positives must not remove a shipped true positive to get there.
#
# Those seven keep their shipped semantics EXACTLY: this change adds no
# preprocessing step, so there is nothing that can reach them at all.
#
# A THIRD rule was designed and WITHDRAWN: a cross-kind disjunction
# ("rejected with HTTP 403 or app error '...'", the run's TC-001). Matching it
# means running a window from the status code to an outcome noun, and four
# review rounds each found one more way for that window to cross into the next
# clause and report a shape this module deliberately leaves alone ("a success
# banner or toast"). Punctuation bounds, then a conjunction list, each closed
# the strings that had been cited and left the class. TC-001 is recorded as a
# known residual instead. Reviving it needs a clause splitter, not a wider
# window -- see the plan's section on what was withdrawn and why.
_DISJUNCTION_PATTERNS: list[re.Pattern] = [
    # "'Monthly spending limit reached' or equivalent cap exceeded message" --
    # one concrete string, then any other string licensed as well.
    # The lookahead is the bilingual whitelist: "or an equivalent Arabic string"
    # names ONE outcome in two locales. "or AR equivalent" needs no lookahead --
    # "AR " is not "a|an" -- so do not relax the "an?" to "\w+".
    re.compile(
        r"\bor\s+(?:an?\s+)?(?:equivalent|similar|comparable)\b"
        r"(?!\s+(?:AR|EN|Arabic|English|localiz\w+|localis\w+|translat\w+)\b)",
        re.IGNORECASE,
    ),
    # "Save button disabled or shows inline validation" -- a state adjective
    # disjoined with a verb CLAUSE, i.e. two oracles of different kinds.
    # BOTH ends are required. Anchoring on the trailing verb alone reported
    # every negated list -- "No banner or prompt is displayed", "no warning or
    # prompt shows" -- and a negated list is the most deterministic assertion
    # shape there is: inside a negative scope BOTH disjuncts must hold.
    # The verb must be a DISPLAY verb, and it must display SOMETHING.
    # "disabled or blocks further taps", "hidden or displays nothing", "greyed
    # or displays no message" all restate the adjective rather than offering a
    # second outcome -- that is "hidden or disabled", the whitelisted shape.
    # The negation guard cannot help here: the negation follows the "or".
    #
    # The second lookahead is the bilingual whitelist, and it must scan the
    # whole clause because the locale token comes AFTER the verb ("disabled or
    # displays the Arabic equivalent tooltip"). Clause-bounded and nothing
    # else -- no character budget and no conjunction list, the two devices that
    # were defeated one word at a time for five rounds.
    re.compile(
        r"\b(?:disabled|hidden|greyed|grayed|enabled|visible|blocked|"
        r"unavailable|inactive|dimmed)\s+or\s+(?:the\s+\w+\s+|it\s+)?"
        r"(?:shows?|displays?|prompts?|returns?|triggers?)\s+"
        r"(?!" + _NEGATION_WORDS + r"\b)"
        r"(?![^.;,]*" + _LOCALE_RENDERING + r")",
        re.IGNORECASE,
    ),
]

# Escape-hatch phrasing (2026-09-26, v1.98 scope C2): a second, INDEPENDENT
# check inside _is_nondeterministic, run alongside (not merged into) the two
# lists above. Both markers here are closed GRAMMATICAL/idiom classes, not
# domain vocabulary tied to one observed suite:
#   - "if applicable" / "where applicable" / "if present" / "where present" is
#     a fixed English hedge idiom: whatever follows may not apply at all, so
#     the case accepts its own absence as much as its presence.
#   - "unless <adjective>" reuses, byte for byte, the build-state adjective set
#     _CONDITIONAL_ACTION_RE below already uses for the analogous problem on
#     ACTIONS (a tester cannot establish these before running the step). This
#     is the same list, applied here to the ORACLE side instead.
# A companion "verb-or-state" rule (a bounded-window aux...or...aux, e.g. "is
# ... or ... is") was designed alongside this one and REJECTED: it is the same
# windowed-disjunction shape that took four review rounds to withdraw below
# (see _WITHDRAWN_CROSS_KIND in tests/test_suite_consistency.py) -- e.g.
# "Response is HTTP 403 and the error banner or toast is shown" plausibly
# matches an aux-before-"or"-aux-after window and MUST stay unreported.
# Reviving that shape needs the clause splitter this module's own comments
# already call for (see the withdrawn cross-kind rule above), not another
# window over a different word list.
_ESCAPE_HATCH_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(?:if|where)\s+(?:applicable|present)\b", re.IGNORECASE),
    re.compile(
        r"\bunless\b[^.]{0,120}?\b(?:visible|available|exposed|present|shown|"
        r"enabled|applicable|offered|supported|exists)\b",
        re.IGNORECASE,
    ),
]

# An expected result may legitimately list two RENDERINGS of one outcome
# ("hidden or disabled", "403 or 404"). Those are not disjunctive oracles, so a
# match containing only such an alternation is not reported.
_EQUIVALENT_RENDERING_RE = re.compile(
    r"\b(?:hidden|disabled|greyed|grayed)\s+or\s+(?:hidden|disabled|greyed|grayed)\b"
    r"|\b\d{3}\s*(?:/|or)\s*\d{3}\b"
    r"|\b(?:AR|EN|Arabic|English)\s+equivalent\b"
    # "in either Arabic or English" is ONE outcome rendered per locale, not two
    # outcomes. This project mandates bilingual cases, so without this the
    # advisory fired on ordinary expected results.
    r"|\b(?:in\s+)?either\s+(?:AR|EN|Arabic|English)\s+or\s+(?:AR|EN|Arabic|English)\b",
    re.IGNORECASE,
)


# An "or" inside a NEGATIVE scope -- "no PAN or CVV is echoed", "no banner or
# prompt is displayed", "no stack trace or SQL text". Both disjuncts must hold
# there, so this is the most deterministic assertion shape a suite contains --
# and it is the false positive that EVERY widening of the rules above has
# produced (six such strings against an early state-or-clause rule, five more
# against an early HTTP rule, each one word away from a real expected result in
# the 2026-08-16 run). It is stripped before the re-check, exactly like the
# equivalent-rendering alternations.
#
# SCOPE, and read this before adding a rule: this guard applies to
# _DISJUNCTION_PATTERNS ONLY. It does NOT cover the seven rules in
# _NONDETERMINISTIC_PATTERNS, and
# a new rule added there is NOT guarded. That is deliberate -- the guard begins
# at a negation, and "if no <channel> ... record it as blocked" is anchored on
# one, so guarding it deleted that shipped detection outright. Put a new
# disjunction rule in _DISJUNCTION_PATTERNS to be guarded.
#
# Clause-bounded: a "." or ";" ends the negative scope, so a genuine disjunction
# in a LATER clause is still seen.
#
# The 45-character window is measured, not guessed: the longest negated list in
# the 2026-08-16 run runs 20 characters from the negation to the "or" ("no full
# PAN, CVV, or password"), and 45 leaves room for a longer list of the same
# shape while declining to reach across a whole sentence. The cost is real and
# is pinned as a known miss: a GENUINE disjunction that follows a negation
# within the same clause and inside that window is eaten too.
_NEGATED_DISJUNCTION_RE = re.compile(
    r"\b" + _NEGATION_WORDS + r"\b[^.;]{0,45}?\bor\b[^.;]{0,50}",
    re.IGNORECASE,
)
