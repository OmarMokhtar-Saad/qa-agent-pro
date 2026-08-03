"""Cross-case consistency gates for a generated suite (grounding Phase 5).

``tools/quality_checks`` judges each step in isolation -- is the phrasing vague,
is the data a placeholder. Two failure modes survive that check untouched
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

Both checks are deterministic, bounded and model-free, and -- like every other
module in ``tools/`` -- never raise to callers.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict

from tools.models import TestCase

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


def _is_nondeterministic(expected: str | None) -> bool:
    if not expected:
        return False
    try:
        if not any(p.search(expected) for p in _NONDETERMINISTIC_PATTERNS):
            return False
        # A single equivalent-rendering alternation is the only "or" present ->
        # one outcome, two wordings. Not a disjunctive oracle.
        stripped = _EQUIVALENT_RENDERING_RE.sub(" ", expected)
        return any(p.search(stripped) for p in _NONDETERMINISTIC_PATTERNS)
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


def consistency_warning_section(cases: list[TestCase]) -> str:
    """Markdown block for unfalsifiable oracles and contradictory assumptions.

    Returns '' when the suite is clean or on any internal error. Never raises.
    """
    try:
        oracles = find_nondeterministic_oracles(cases)
        conflicts = find_contradictory_state_assumptions(cases)
        if not oracles and not conflicts:
            return ""
        lines = ["\n\n## Suite Consistency (advisory)\n"]
        if oracles:
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
        if conflicts:
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
        return "\n".join(lines)
    except Exception:
        logger.exception("consistency_warning_section failed - returning empty string")
        return ""
