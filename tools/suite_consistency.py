"""Cross-case consistency gates for a generated suite (grounding Phase 5).

``tools/quality_checks`` judges each step in isolation -- is the phrasing vague,
is the data a placeholder. Four failure modes survive that check untouched
because they are properties of the SUITE, not of any single step:

1. **An oracle that cannot fail.** "Either the cancel succeeds or validation
   blocks it", "if Processing is cancel-eligible in this build, note the
   exception and stop". Both branches pass, so the case can never report a
   defect -- it consumes a tester's time and returns no signal. This is the
   oracle problem showing up as a disjunction: when the source specifies no
   outcome, the honest artifact is an exploratory charter, not a scripted case
   that accepts everything.

2. **Two cases that contradict each other.** One case seeds order status
   ``Processing`` as cancellable, another asserts ``Processing`` blocks cancel.
   Whichever way the build behaves, one of them files a false defect. The
   requirement is genuinely silent, so this is reported for a human to resolve
   rather than guessed at.

3. **An action that cannot be executed as written.** "Tap 'Card controls' if
   visible", "review the audit trail entry if exposed". The oracle may be
   perfect; the tester still cannot tell whether the step applies to this
   build, so a Pass and a silent skip are indistinguishable in the report.

4. **A value that contradicts a bound the source states.** The source allows a
   monthly cap "between SAR 1,000 and SAR 200,000"; a case enters 99,999 --
   inside that range -- and asserts the Save button is disabled. The case
   fails against correct software. Delegated to ``tools/bound_consistency``,
   which needs the source text the other three do not.

All four checks are deterministic, bounded and model-free, and -- like every
other module in ``tools/`` -- never raise to callers.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict

from tools.bound_consistency import find_bound_contradictions
from tools.coverage_classes import find_missing_coverage_classes
from tools.models import TestCase
from tools.oracle_grounding import find_ungrounded_ui_strings

logger = logging.getLogger(__name__)

# Cap on reported findings so a pathological suite cannot produce an unbounded
# markdown block.
_MAX_REPORTED = 20

# Expected-result phrasing that accepts MORE THAN ONE outcome. Each pattern is
# anchored on an explicit disjunction or an explicit "give up and record it"
# escape -- not on the mere presence of "or", which appears legitimately in
# "the Cancel order option is hidden or disabled" (one outcome, two renderings).
_NONDETERMINISTIC_PATTERNS: list[re.Pattern] = [
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


def _is_nondeterministic(expected: str | None) -> bool:
    if not expected:
        return False
    try:
        # A single equivalent-rendering alternation is the only "or" present ->
        # one outcome, two wordings. Not a disjunctive oracle.
        stripped = _EQUIVALENT_RENDERING_RE.sub(" ", expected)
        if any(p.search(expected) for p in _NONDETERMINISTIC_PATTERNS) and any(
            p.search(stripped) for p in _NONDETERMINISTIC_PATTERNS
        ):
            return True
        # The 2026-08-16 rules, and ONLY those, additionally discount a
        # disjunction that sits inside a negative scope: in "no X or Y" both
        # disjuncts must hold, so it asserts one outcome. The seven rules above
        # are deliberately NOT run through this -- one of them is anchored on a
        # negation, and the guard would delete the detection it exists for.
        if not any(p.search(expected) for p in _DISJUNCTION_PATTERNS):
            return False
        neutral = _NEGATED_DISJUNCTION_RE.sub(" ", stripped)
        return any(p.search(neutral) for p in _DISJUNCTION_PATTERNS)
    except Exception:
        return False


def find_nondeterministic_oracles(cases: list[TestCase]) -> list[tuple[str, int, str]]:
    """(tc_id, step_number, expected_result) for every unfalsifiable oracle.

    A step is reported when its expected result accepts two opposite outcomes,
    or defers to "record whatever happens" -- in both cases the step cannot
    fail, so it cannot detect a defect. Never raises.
    """
    try:
        return [
            (tc.tc_id, step.step_number, step.expected_result)
            for tc in cases
            for step in (tc.steps or [])
            if _is_nondeterministic(step.expected_result)
        ]
    except Exception:
        logger.exception("find_nondeterministic_oracles failed - returning empty list")
        return []


# --- Conditional actions ----------------------------------------------------

# An ACTION whose performance depends on what the build happens to show. This is
# a DIFFERENT defect from a soft oracle: there the tester runs the step and
# cannot fail it, here the tester cannot tell whether to run the step at all, so
# a Pass and a silent skip are indistinguishable in the report.
#
# TWO things control this, and the "if" is neither of them:
#
# 1. the ADJECTIVE VOCABULARY -- every listed word describes what the BUILD
#    exposes, so "Tap Freeze if the toggle is enabled" fires on purpose (same
#    defect, same remedy) while "Tap Save if the form is complete" stays quiet:
#    "complete" is a state the tester can establish. Add a word here only if a
#    tester could NOT establish it before running the step.
# 2. CLAUSE POSITION -- the adjective must END its clause. Without this the
#    pattern reads "if <adjective> <noun>" as a predicate and fires on "if
#    visible latency is observed", "if available balance is zero", "if the
#    endpoint is supported by this API version" -- ordinary steps whose
#    condition is about the DATA, not about whether the step can be run at all.
#
#    A locative exemption was tried and WITHDRAWN. Allowing "if present IN the
#    audit log" re-opened the predicate class twice: a bare "in|on|for"
#    terminator let every one of those three strings back in with one inserted
#    preposition ("if visible ON THE ROW latency is observed"), and requiring
#    the locative to close the clause only moved the hole to any SHORT tail
#    ("if available in SAR balance is zero."). Distinguishing the two needs a
#    finite-verb test, which is not a regex's job. The known cost is recorded
#    in test_locative_conditional_is_a_known_miss: "Note the result if present
#    in the audit log" is NOT reported. Zero of the run's 159 actions use that
#    phrasing, and a false positive here costs more than this miss.
#
#    The clause end is PUNCTUATION or the end of the string, and nothing else.
#    A word terminator re-admits the predicate reading through the very gap the
#    locative exemption was withdrawn for: "Check if available AND current
#    balance is zero, then tap Save" reads "available" as a predicate over
#    "current balance", and "and"/"or"/"then" were each one inserted word away
#    from a pinned negative. Round 4, 6, 7 and 8 each closed one instance of
#    this class; this closes the class. Its cost is one phrasing, pinned in
#    test_a_conjunction_tail_conditional_is_a_known_miss.
_CONDITIONAL_ACTION_RE = re.compile(
    r"\bif\s+(?:it\s+is\s+|its\s+|the\s+\w+\s+is\s+)?"
    r"(?:visible|available|exposed|present|shown|enabled|applicable|offered|"
    r"supported|exists)\b(?=\s*(?:[,.;)]|$))",
    re.IGNORECASE,
)


def find_conditional_actions(cases: list[TestCase]) -> list[tuple[str, int, str]]:
    """(tc_id, step_number, action) for every step whose ACTION is conditional.

    :func:`find_nondeterministic_oracles` reads only ``expected_result``, so a
    step reading "Tap 'Card controls' if visible" passed every gate in the tree
    while being unrunnable as written. Never raises.
    """
    try:
        return [
            (tc.tc_id, step.step_number, step.action)
            for tc in cases
            for step in (tc.steps or [])
            if step.action and _CONDITIONAL_ACTION_RE.search(step.action)
        ]
    except Exception:
        logger.exception("find_conditional_actions failed - returning empty list")
        return []


# --- Contradictory state assumptions ---------------------------------------

# A test_data field naming the state/status an entity is seeded in.
_STATE_FIELD_RE = re.compile(r"stat(?:us|e)", re.IGNORECASE)

# The case expects the action to BE offered.
_ACTION_AVAILABLE_RE = re.compile(
    r"\b(?:is|remains|stays|still)\s+(?:visible|available|enabled|offered|shown|present|tappable)\b"
    r"|\bis\s+visible\s+and\s+enabled\b",
    re.IGNORECASE,
)
# The case expects the action NOT to be offered.
_ACTION_BLOCKED_RE = re.compile(
    r"\bis\s+not\s+(?:visible|available|offered|shown|present)\b"
    r"|\bis\s+(?:hidden|disabled|unavailable)\b"
    r"|\bno\s+longer\s+(?:shown|offered|available)\b"
    r"|\bcannot\s+(?:open|start|be\s+cancel\w+)\b",
    re.IGNORECASE,
)

# Terminal states where "the action is gone" is the CORRECT expectation for
# every case, so an available/blocked split across them is not a contradiction.
_TERMINAL_STATE_TOKENS = {
    "cancelled",
    "canceled",
    "ملغي",
    "completed",
    "closed",
    "refunded",
}


# A `key: value` pair inside a step's free-text test_data string. Status is
# frequently carried there rather than as a structured item -- "Step 1: order_id:
# ORD-KEEP-001, initial_status: Processing" -- so both sources are scanned.
_INLINE_PAIR_RE = re.compile(r"([A-Za-z][A-Za-z0-9_ ]{0,30})\s*:\s*([^,;\n]{1,40})")


def _is_state_value(value: str) -> bool:
    """True when a value looks like a state label rather than a seeded id."""
    v = (value or "").strip().lower()
    if not v or len(v) > 40:
        return False
    # A seeded identifier ("ord-cancel-locked-001", "seed_order_shared_008").
    if re.fullmatch(r"[a-z0-9\-_]*\d{2,}[a-z0-9\-_]*", v):
        return False
    return bool(re.search(r"[a-z؀-ۿ]", v))


def _seeded_states(tc: TestCase) -> set[str]:
    """Normalized state values this case seeds.

    Reads BOTH the structured ``test_data`` items and each step's free-text
    ``test_data`` string: generators put the seeded status in either place, and
    reading only the structured items missed every case that wrote
    "initial_status: Processing" into a step.
    """
    out: set[str] = set()
    try:
        for item in getattr(tc, "test_data", None) or []:
            field = getattr(item, "field", "") or ""
            if not _STATE_FIELD_RE.search(field):
                continue
            value = (getattr(item, "example_value", "") or "").strip().lower()
            if _is_state_value(value):
                out.add(value)
        for step in getattr(tc, "steps", None) or []:
            blob = getattr(step, "test_data", None) or ""
            for key, value in _INLINE_PAIR_RE.findall(blob):
                if not _STATE_FIELD_RE.search(key):
                    continue
                normalized = value.strip().lower()
                if _is_state_value(normalized):
                    out.add(normalized)
    except Exception:
        logger.exception("_seeded_states failed - returning what was found")
    return out


# Words that carry no identity when comparing which control a sentence is about.
_SUBJECT_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "no",
        "not",
        "and",
        "or",
        "but",
        "for",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "on",
        "in",
        "at",
        "to",
        "of",
        "with",
        "from",
        "still",
        "remains",
        "stays",
        "longer",
        "any",
        "all",
        "user",
        "users",
        "screen",
        "page",
        "app",
        # Structural words that appear in almost every expected result here, so
        # an overlap on them says nothing about the two cases being related --
        # "status" alone was matching a pair that shared no control at all.
        "status",
        "state",
        "step",
        "current",
        "per",
        "rules",
        "rule",
        "build",
        "equivalent",
        "shown",
        "unchanged",
        "detail",
        "details",
        "option",
    }
)
# NOTE: the apostrophe is deliberately NOT part of a word. Quoted control names
# are the norm in these expected results ("'Cancel order' is still visible"), and
# including the quote produced tokens like "'cancel" / "order'" that could never
# match the same words written unquoted -- silently disabling the whole check.
_SUBJECT_WORD_RE = re.compile(r"[A-Za-z\u0600-\u06ff]+")


def _subject_terms(text: str, match: re.Match) -> set[str]:
    """Identity words of the control a polarity phrase is talking about.

    Taken from the words immediately BEFORE the match, which in practice is the
    subject noun phrase ("the 'Cancel order' option is visible" -> cancel,
    order, option).
    """
    prefix = text[max(0, match.start() - 90) : match.start()]
    words = [w.lower().strip("'\"") for w in _SUBJECT_WORD_RE.findall(prefix)]
    return {w for w in words[-6:] if w and w not in _SUBJECT_STOPWORDS and len(w) > 2}


def _polarity_of(text: str) -> tuple[str, set[str]] | None:
    """(polarity, subject terms), or None when the text asserts neither/both.

    The subject terms exist because polarity ALONE is not a contradiction: a
    suite that says "a success toast is visible" in one case and "the refund
    banner is not shown" in another was reported as disagreeing about whether the
    seeded status permits the action, which it plainly was not. Two cases now
    only conflict when they speak about the same control.
    """
    blocked = list(_ACTION_BLOCKED_RE.finditer(text))
    available = list(_ACTION_AVAILABLE_RE.finditer(text))

    def subjects(matches: list[re.Match]) -> set[str]:
        # EVERY match contributes, not just the first: a case that says both
        # "success screen is not shown" and "Cancel is unavailable" is about the
        # cancel control too, and reading only the first match dropped two of the
        # three cases in the real Processing contradiction.
        out: set[str] = set()
        for match in matches:
            out |= _subject_terms(text, match)
        return out

    if blocked and not available:
        return "blocked", subjects(blocked)
    if available and not blocked:
        return "available", subjects(available)
    return None


def _expected_polarity(tc: TestCase) -> str | None:
    """'available' / 'blocked' / None -- what the case expects of the action.

    When the case asserts both across its steps, the FINAL step decides: that is
    the case's conclusion, and earlier steps routinely describe the starting
    state ("Cancel order is available") before the action changes it. A case
    whose last step is itself ambiguous yields None and takes no part in
    contradiction detection -- it is already reported by
    :func:`find_nondeterministic_oracles`.
    """
    try:
        steps = list(getattr(tc, "steps", None) or [])
        if not steps:
            return None
        joined = " ".join((s.expected_result or "") for s in steps)
        overall = _polarity_of(joined)
        if overall is not None:
            return overall
        return _polarity_of(steps[-1].expected_result or "")
    except Exception:
        return None


def find_contradictory_state_assumptions(
    cases: list[TestCase],
) -> list[tuple[str, list[str], list[str]]]:
    """(state_value, tc_ids expecting available, tc_ids expecting blocked).

    Reports each seeded state that one case treats as allowing the action while
    another treats it as blocking the action. Terminal states (``cancelled``,
    ``completed``) are excluded: there, "the action is gone" is what every case
    should expect, so a mixed reading is not a conflict.

    On the SHYJ-5645 suite this reports ``processing`` -- seeded as the
    cancellable status by three cases and as the blocking status by a fourth,
    which the ticket never resolves. Never raises.
    """
    out: list[tuple[str, list[str], list[str]]] = []
    try:
        available: dict[str, list[tuple[str, frozenset[str]]]] = defaultdict(list)
        blocked: dict[str, list[tuple[str, frozenset[str]]]] = defaultdict(list)
        for tc in cases or []:
            resolved = _expected_polarity(tc)
            if resolved is None:
                continue
            polarity, subject = resolved
            if not subject:
                continue
            tc_id = getattr(tc, "tc_id", "") or ""
            for state in _seeded_states(tc):
                if any(token in state for token in _TERMINAL_STATE_TOKENS):
                    continue
                bucket = available if polarity == "available" else blocked
                bucket[state].append((tc_id, frozenset(subject)))
        for state in sorted(set(available) & set(blocked)):
            # Only a shared subject makes this a contradiction rather than two
            # unrelated assertions that happen to seed the same status.
            yes = sorted(
                {
                    tc_id
                    for tc_id, subj in available[state]
                    if any(subj & other for _o, other in blocked[state])
                }
            )
            no = sorted(
                {
                    tc_id
                    for tc_id, subj in blocked[state]
                    if any(subj & other for _o, other in available[state])
                }
            )
            if yes and no:
                out.append((state, yes, no))
    except Exception:
        logger.exception(
            "find_contradictory_state_assumptions failed - returning what was found"
        )
    return out


def _amount(value: float) -> str:
    """Thousands-separated, and without a trailing '.0' on a whole number."""
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,}"


# The whole block's byte budget. NOT a new number: it is the bound
# tests/test_suite_consistency.py has asserted since 2026-08-17, which nothing
# enforced. That bound was measured with two of the six bullets structurally
# absent -- both F05 (ungrounded UI strings) and F11 (bound contradictions) need
# a grounding text over oracle_grounding._MIN_GROUNDING_CHARS, and the test
# passed none -- so the block reached 4173 bytes in a grounded run against a
# 2400 assertion, past mcp_handlers._SUMMARY_CAP (4000) outright.
#
# Why the block and not each bullet: every bullet already caps its own ROW COUNT
# at _MAX_REPORTED, and that is exactly the per-bullet framing that produced this
# -- six individually-bounded bullets whose SUM nobody bounded. F14 is the
# finding that says so.
_BLOCK_CAP = 2400

# Drop priorities for the advisory bullets. LOWER drops FIRST, and each constant
# names WHY that bullet is droppable at all -- a bullet that cannot answer that
# question is protected instead. Mirrors mcp_handlers._REPLY_P_* deliberately,
# including the rule that the rationale is part of the constant.
#
# The axis is: does the bullet say the suite is WRONG, or that it is INCOMPLETE?
# A wrong case misleads a tester who runs it. A thin suite does not.
_ADV_P_SUGGESTION = 1  # names work not done, not a defect in work done
_ADV_P_CROSS_CASE = 2  # a relationship BETWEEN cases; each alone still runs
_ADV_P_UNEXECUTABLE = 3  # the case cannot be run -- visible on first attempt
_ADV_P_UNJUDGEABLE = 4  # the case runs but its outcome cannot be judged


def _advisory_omission_marker(names: list[str]) -> str:
    """Disclose which advisory bullets the block budget dropped, BY NAME.

    Deliberately NOT mcp_handlers._omission_marker, which is the reply-level
    equivalent. That one promises the reader that what it dropped "repeats
    something you can still reach (the export, the coverage notes, or a tool you
    can call on demand)". For an advisory bullet that promise is FALSE: these
    findings are written nowhere else -- not into the workbook, which carries the
    cases and (since F06) their requirement ids, but no advisory. So this marker
    makes the opposite admission, and says how to get the dropped bullet back.
    """
    return (
        "\n  - \u2139\ufe0f  "
        f"{len(names)} further advisory bullet(s) were left out to keep this "
        f"block within its budget: {', '.join(names)}. Unlike the sections the "
        "reply drops, these are NOT written into the export -- re-run the "
        "submit against a smaller suite to see them."
    )


# The example floor. Under budget pressure a bullet's rows shrink to this before
# any bullet is dropped, because a bullet's HEADLINE is the finding ("96 case(s)
# have an action the tester cannot execute as written") and its rows are EXAMPLES
# of it. A lost example costs the tester one tc_id they can re-derive from the
# suite; a lost headline costs them the knowledge that the problem exists.
# Examples are re-derivable, findings are not, so examples are spent first.
#
# Measured 2026-08-20 on an adversarial 96-case suite: at the full row cap the
# block kept ONE finding and dropped three; trimming rows instead keeps all four
# in 2114 bytes, under _BLOCK_CAP, so the drop path never runs at all.
_MIN_ROWS_SHOWN = 3
_ROW_PREFIX = "  - "
_MORE_ROW_RE = re.compile(r"^  - \.\.\. and (\d+) more$")


def _trim_bullet_rows(bullet: list[str], row_cap: int) -> list[str]:
    """One bullet with its example rows reduced to *row_cap*.

    The ``... and N more`` line is RECOMPUTED, never left stale: N becomes the
    count it already carried plus the rows this trim removed. A row list that is
    trimmed but still claims the old overflow number is a worse defect than the
    budget overrun this exists to prevent, so the arithmetic is the thing the
    tests assert.

    A bullet with no overflow line gains one when trimmed -- which is how the
    contradictory-state bullet (which slices with no disclosure at all) gets an
    honest count on this path. Returns the bullet unchanged when it already fits.
    """
    rows = [
        ln
        for ln in bullet[1:]
        if ln.startswith(_ROW_PREFIX) and not _MORE_ROW_RE.match(ln)
    ]
    if len(rows) <= row_cap:
        return bullet
    already = 0
    for ln in bullet[1:]:
        match = _MORE_ROW_RE.match(ln)
        if match:
            already = int(match.group(1))
    hidden = already + (len(rows) - row_cap)
    return [bullet[0]] + rows[:row_cap] + [f"  - ... and {hidden} more"]


def _bound_advisory_block(lines: list[str], order: list[tuple[str, int]]) -> str:
    """Join *lines* into the advisory block, bounded by ``_BLOCK_CAP``.

    *order* is (name, priority) for each bullet actually rendered, in the order
    it was appended -- priority ``0`` means protected. Bullets are split off the
    assembled lines by their ``- **`` markers rather than being buffered
    separately, so every bullet body above is untouched.

    UNDER budget the result is byte-identical to ``"\\n".join(lines)``: that fast
    path is the point, so every assertion pinned on today's block stays true.
    OVER budget the order of sacrifice is EXAMPLES, then findings: every bullet's
    row list shrinks toward ``_MIN_ROWS_SHOWN`` first, keeping every headline;
    only if the floor still does not fit do whole bullets drop, in strict
    priority order and never truncated mid-bullet, because half a finding with a
    tc_id and no verdict is worse than a named absence. Protected bullets never
    drop. Never raises.
    """
    try:
        joined = "\n".join(lines)
        if len(joined) <= _BLOCK_CAP:
            return joined
        # Split into [header, bullet, bullet, ...] on the top-level markers.
        groups: list[list[str]] = [[]]
        for line in lines:
            if line.startswith("- **"):
                groups.append([])
            groups[-1].append(line)
        header, bullets = groups[0], groups[1:]
        if len(bullets) != len(order):  # pragma: no cover - defensive
            return joined
        # SPEND EXAMPLES BEFORE FINDINGS. Shrink every bullet's row list toward
        # _MIN_ROWS_SHOWN, checking fit after each step, and only fall through to
        # dropping whole bullets if even the floor does not fit. Uniform across
        # bullets rather than largest-first: the largest bullet is not the least
        # important one, and per-bullet favouritism is the framing F14 faulted.
        for row_cap in (12, 8, 6, 4, _MIN_ROWS_SHOWN):
            trimmed = [_trim_bullet_rows(b, row_cap) for b in bullets]
            body = "\n".join(header + [ln for b in trimmed for ln in b])
            if len(body) <= _BLOCK_CAP:
                # Never let trimming make the block longer than leaving it alone.
                return body if len(body) < len(joined) else joined
            bullets = trimmed
        keep = list(range(len(bullets)))
        dropped: list[int] = []
        for idx in sorted(
            (i for i, (_n, p) in enumerate(order) if p),
            key=lambda i: (order[i][1], i),
        ):
            body = "\n".join(
                header + [ln for j in keep if j != idx for ln in bullets[j]]
            )
            reserve = len(
                _advisory_omission_marker([order[j][0] for j in dropped + [idx]])
            )
            keep.remove(idx)
            dropped.append(idx)
            if len(body) + reserve <= _BLOCK_CAP:
                break
        if not dropped:
            return joined
        out = "\n".join(
            header + [ln for j in keep for ln in bullets[j]]
        ) + _advisory_omission_marker([order[j][0] for j in sorted(dropped)])
        # Dropping may never make the block LONGER than leaving it alone -- the
        # same net-loss rule mcp_handlers.assemble_finalize_reply enforces.
        return out if len(out) < len(joined) else joined
    except Exception:
        logger.exception("advisory block budget failed - returning it unbounded")
        return "\n".join(lines)


def consistency_warning_section(
    cases: list[TestCase], *, grounding_text: str = ""
) -> str:
    """Markdown block for the suite-level advisories.

    ``grounding_text`` is the SOURCE the suite was generated from. It is
    keyword-only with an empty default on purpose: every caller that
    predates the invented-UI-string bullet keeps its exact behaviour, and
    an empty string makes ``find_ungrounded_ui_strings`` report nothing --
    checking assertions against a source you do not have flags all of them.

    Returns '' when the suite is clean or on any internal error. Never raises.
    """
    try:
        oracles = find_nondeterministic_oracles(cases)
        conditional = find_conditional_actions(cases)
        conflicts = find_contradictory_state_assumptions(cases)
        ungrounded = find_ungrounded_ui_strings(cases, grounding_text)
        bounds = find_bound_contradictions(cases, grounding_text)
        classes = find_missing_coverage_classes(cases)
        if (
            not oracles
            and not conditional
            and not conflicts
            and not ungrounded
            and not bounds
            and not classes
        ):
            return ""
        lines = ["\n\n## Suite Consistency (advisory)\n"]
        # (name, priority) per bullet, appended in the same order the bullets
        # are. Priority 0 == protected: the two bullets that say a case is WRONG
        # rather than missing, and so can never be the ones that yield.
        order: list[tuple[str, int]] = []
        if oracles:
            order.append(("soft oracles", _ADV_P_UNJUDGEABLE))
            by_case: dict[str, list[int]] = defaultdict(list)
            for tc_id, step_number, _ in oracles:
                by_case[tc_id].append(step_number)
            lines.append(
                f"- **{len(by_case)} case(s) have an expected result that accepts more "
                "than one outcome**, so the step cannot fail and cannot detect a "
                "defect. Where the ticket genuinely does not specify the outcome, "
                "convert the case to an exploratory charter that records the actual "
                "behaviour instead of asserting one:"
            )
            for tc_id in sorted(by_case)[:_MAX_REPORTED]:
                steps = ", ".join(str(n) for n in sorted(by_case[tc_id]))
                lines.append(f"  - {tc_id} (step {steps})")
            if len(by_case) > _MAX_REPORTED:
                lines.append(f"  - ... and {len(by_case) - _MAX_REPORTED} more")
        if conditional:
            order.append(("conditional actions", _ADV_P_UNEXECUTABLE))
            # Its own bullet, not merged into the oracle list: a soft oracle
            # means the tester runs the step and cannot fail it, a conditional
            # action means the tester cannot tell whether to run it at all, and
            # one count would misdescribe both. Same block, because a fourth
            # heading costs reply budget for advice of the same class.
            by_action: dict[str, list[int]] = defaultdict(list)
            for tc_id, step_number, _ in conditional:
                by_action[tc_id].append(step_number)
            lines.append(
                f"- **{len(by_action)} case(s) have an action the tester "
                'cannot execute as written** \u2014 it is conditional ("if '
                'visible", "if available"), so whether the step is performed '
                "at all depends on what the build happens to show, and a Pass "
                "is indistinguishable from a skip. Name the entry point the "
                "case means, or split the conditional branch into its own case:"
            )
            for tc_id in sorted(by_action)[:_MAX_REPORTED]:
                numbers = ", ".join(str(n) for n in sorted(by_action[tc_id]))
                lines.append(f"  - {tc_id} (step {numbers})")
            if len(by_action) > _MAX_REPORTED:
                lines.append(f"  - ... and {len(by_action) - _MAX_REPORTED} more")
        if conflicts:
            order.append(("contradictory state assumptions", _ADV_P_CROSS_CASE))
            lines.append(
                "- **Contradictory state assumptions** — the same seeded state is "
                "treated as both allowing and blocking the action. The ticket does "
                "not resolve this; confirm the rule before executing either side:"
            )
            for state, avail, block in conflicts[:_MAX_REPORTED]:
                lines.append(
                    f"  - `{state}`: allowed by {', '.join(avail)} / "
                    f"blocked by {', '.join(block)}"
                )
        if ungrounded:
            # PROTECTED. This bullet says the case asserts copy the product never
            # promised -- a false oracle a tester files a bug against. It is also
            # the grounding/trust signal, and the bullet whose silent absence
            # made the original bound vacuous; it must never be the one dropped.
            order.append(("invented UI strings", 0))
            # A FOURTH bullet in this block rather than a heading of its
            # own, for the reason recorded above the conditional-action
            # bullet: this is the same class of advice -- "this assertion
            # cannot do its job" -- and a second heading costs finalize-
            # reply budget that F03 already measured as scarce.
            #
            # Grouped by the invented STRING, not by tc_id like the two
            # bullets above, because the string IS the defect: suite
            # b8b8ed00 repeats one invented 'Transfer scheduled' across
            # all 96 of its cases, and grouping by case would spend twenty
            # lines of the reply saying the same thing twenty times.
            by_span: dict[str, set[str]] = defaultdict(set)
            for tc_id, _step_number, span in ungrounded:
                by_span[span].add(tc_id)
            lines.append(
                f"- **{len(by_span)} exact UI string(s) are asserted that "
                "the source never promises** \u2014 the tester compares "
                "the product against copy nobody wrote, so any wording "
                "difference reads as a defect. Assert what the message "
                'MUST SAY ("an error that names the field and the limit it '
                'broke") instead '
                "of its exact text, or get the real copy into the ticket:"
            )
            for span in sorted(by_span)[:_MAX_REPORTED]:
                shown = span if len(span) <= 60 else span[:57] + "..."
                ids = sorted(by_span[span])
                where = ids[0] if len(ids) == 1 else f"{ids[0]} +{len(ids) - 1} more"
                lines.append(f"  - '{shown}' ({where})")
            if len(by_span) > _MAX_REPORTED:
                lines.append(f"  - ... and {len(by_span) - _MAX_REPORTED} more")
        if bounds:
            # PROTECTED, same class as the bullet above: the case asserts that a
            # documented-legal value is rejected, so it fails against correct
            # software. Wrong, not thin.
            order.append(("bound contradictions", 0))
            # A FIFTH bullet, same block, for the reason recorded above the
            # conditional-action bullet: same class of advice ("this case
            # cannot pass against correct software"), and a heading of its own
            # costs finalize-reply budget F03 already measured as scarce.
            lines.append(
                f"- **{len(bounds)} case(s) contradict a numeric bound the "
                "source states** \u2014 the value the step enters is legal under "
                "the range the source gives for that field yet the step asserts "
                "it is rejected (or the reverse), so the case fails against "
                "correct software. Check the value, and check it is on the "
                "field the range actually belongs to:"
            )
            for finding in bounds[:_MAX_REPORTED]:
                side = "inside" if finding.kind == "legal-rejected" else "outside"
                verdict = (
                    "the step expects it to be rejected"
                    if finding.kind == "legal-rejected"
                    else "the step expects it to be accepted"
                )
                lines.append(
                    f"  - {finding.tc_id} (step {finding.step_number}): "
                    f"{_amount(finding.value)} is {side} the "
                    f"{_amount(finding.low)}\u2013{_amount(finding.high)} the source "
                    f"states for the {finding.subject}, but {verdict}"
                )
            if len(bounds) > _MAX_REPORTED:
                lines.append(f"  - ... and {len(bounds) - _MAX_REPORTED} more")
        if classes:
            order.append(("missing coverage classes", _ADV_P_SUGGESTION))
            # A SIXTH bullet, same block, same reasoning as the ones
            # above: same class of advice, and a heading of its own
            # costs finalize-reply budget F03 measured as scarce.
            #
            # Each line reports QUALIFYING against MENTIONED, because
            # the two numbers say different things and only the pair
            # is actionable: '3 mention, 0 test it' means the subject
            # is covered on the happy path and untested on the failure
            # path, which is the single most common shape of this gap.
            lines.append(
                f"- **{len(classes)} test class(es) an experienced "
                "tester would expect are missing or thin.** Each line "
                "gives how many cases actually exercise the class "
                "against how many merely mention its subject:"
            )
            for finding in classes[:_MAX_REPORTED]:
                got = len(finding.qualifying)
                seen = len(finding.subject_hits)
                tail = (
                    " - the source does not specify the outcome, so "
                    "cover it as an exploratory charter that RECORDS "
                    "the behaviour, never as an asserted result"
                    if finding.unspecified
                    else ""
                )
                lines.append(
                    f"  - {finding.label}: {got} of {finding.floor} "
                    f"case(s), from {seen} that mention the subject{tail}"
                )
            if len(classes) > _MAX_REPORTED:
                lines.append(f"  - ... and {len(classes) - _MAX_REPORTED} more")
        return _bound_advisory_block(lines, order)
    except Exception:
        logger.exception("consistency_warning_section failed - returning empty string")
        return ""
