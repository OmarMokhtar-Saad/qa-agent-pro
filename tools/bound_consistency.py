"""Numeric-bound contradictions between a case and its source (finding F11).

`suite_consistency` asks whether an expected result has a *shape* that can
fail. This module asks a different question: given that the source states a
numeric range for a field, does a case enter a value that the range makes
LEGAL and then assert it is rejected -- or enter an ILLEGAL one and assert it
is accepted? Either way the case fails against correct software, and no other
check in the pipeline reads a number.

On the 2026-08-16 run it reports ``TC-094``: the source allows a monthly
spending cap "between SAR 1,000 and SAR 200,000", the case enters 99,999 --
inside that range -- and asserts the Save button is disabled.

The whole difficulty is BINDING a range to the right field. Getting it wrong
would flag the eight correct Boundary-Values cases in that same suite, which
is a far worse outcome than missing one bad case, so every rule here is
biased towards silence:

* a step binds to a range only when it shares at least
  ``_MIN_SUBJECT_OVERLAP`` DISTINCTIVE subject tokens with exactly one range;
  a tie or a weaker match is dropped rather than guessed at;
* a value is read only after an enter/set verb, and only when it is a wholly
  numeric quoted literal or is currency-prefixed -- a bare integer in an
  action is far more often a card number, an id or a purchase amount
  (``Set credit 8834 per-transaction limit to SAR 2,000``);
* a stated increment ("in increments of SAR 100") makes an in-range value
  illegal, so a case that rejects SAR 1,050 is consistent, not a finding;
* an expected result that reads as both accepting and rejecting, or as
  neither, yields nothing.

Replayed over 5,098 cases from 63 stored suites this emits exactly one
finding, the true positive above.

Scope, deliberately: it does NOT report that ``TC-094`` is tagged ``AC-003``
while testing AC-004's field, even though the binding knows it -- the
requirement-id tag is finding F04's deliverable, and one advisory should not
be emitted twice. Nor does it touch the disjunctive oracle in the same
expected result, which is F02's.

Deterministic, bounded, model-free, and -- like every other module in
``tools/`` -- never raises to callers.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from tools.models import TestCase

logger = logging.getLogger(__name__)

_NUM = r"(\d[\d,]*(?:\.\d+)?)"

# "between SAR 100 and SAR 20,000", "from 1 to 50". The currency/unit token
# before the second number is optional and unconstrained in length only up to
# four characters, so "SAR"/"USD"/"kg" pass and a word does not.
_RANGE_RE = re.compile(
    r"\b(?:between|from)\b[^.;]{0,40}?"
    + _NUM
    + r"\s+(?:and|to)\s+[A-Za-z]{0,4}\s*"
    + _NUM,
    re.IGNORECASE,
)
_INCREMENT_RE = re.compile(r"increments?\s+of\s+[A-Za-z]{0,4}\s*" + _NUM, re.IGNORECASE)

# Words that carry no field identity. Kept small on purpose: the distinctive-
# token step below removes anything two ranges share, so a generic word that
# appears in both subjects drops out without being listed here.
_SUBJECT_STOPWORDS = frozenset(
    """
    the a an of in on for and or with to be is are can cannot from at by that
    this each per set sets setting shows show app user must should within so
    far value values field between above below their its
    """.split()
)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z-]*")

# The value the tester types. A quoted literal must be numeric IN FULL -- the
# SQL payload "100; DROP TABLE cards;--" starts with a legal amount and is not
# one. Otherwise the number must carry a currency marker.
_ENTER_VERB_RE = re.compile(
    r"\b(?:enter|enters|input|inputs|type|types|set|sets)\b", re.IGNORECASE
)
_QUOTED_RE = re.compile(r"['\"‘’“”]([^'\"‘’“”]{1,40})['\"‘’“”]")
_PURE_NUMERIC_RE = re.compile(r"^[A-Za-z]{0,4}\s*\d[\d,]*(?:\.\d+)?$")
_CURRENCY_RE = re.compile(r"(?:[A-Z]{3}|[$£€¥])\s*" + _NUM + r"(?![\w.,]*[A-Za-z])")

_REJECT_RE = re.compile(
    r"\b(?:error|invalid|reject|declin|not saved|unchanged|retained|disabled|"
    r"must be|blocked|fail)",
    re.IGNORECASE,
)
# "not saved" and "never updated" are rejections wearing an acceptance verb;
# without the lookbehinds every negative expected result reads as both
# polarities and the check goes silent on the cases it exists for.
_ACCEPT_RE = re.compile(
    r"(?<!not )(?<!never )\b(?:saved|success|updated|accepted|applied|confirm)",
    re.IGNORECASE,
)

# How many DISTINCTIVE subject tokens a step must share with a range before it
# is bound to it. Two, not one: with one, any step mentioning "limit" binds to
# the per-transaction range, and mis-binding is precisely the mistake TC-094
# itself made.
_MIN_SUBJECT_OVERLAP = 2

# How far past the enter verb the value may sit.
_VALUE_WINDOW = 80


@dataclass(frozen=True)
class BoundFinding:
    """One case step whose value contradicts a bound stated in the source."""

    tc_id: str
    step_number: int
    value: float
    subject: str
    low: float
    high: float
    kind: str  # "legal-rejected" | "illegal-accepted"


@dataclass(frozen=True)
class _Bound:
    low: float
    high: float
    increment: float | None
    subject: frozenset[str]
    distinctive: frozenset[str]
    # The same words as ``subject``, in the order the clause wrote them, so
    # the advisory can name the field rather than list its words.
    order: tuple[str, ...]

    @property
    def label(self) -> str:
        """How the field is NAMED in the advisory -- distinctive words only,
        in the order the SOURCE wrote them.

        Not the raw subject: that carries every content word before the range,
        so the monthly-cap bound printed as "cap cardholder monthly spending"
        and put a stray actor noun into tester-facing text. The distinctive
        set is what identifies the field, and it is what the binding matched
        on, so naming it keeps the report and the decision in step.

        Source order, not sorted: a tester reads "the monthly spending cap",
        not "the cap monthly spending", and the label is the only part of this
        finding that has to be recognised as a FIELD NAME rather than parsed.
        The fragments a hyphenated token contributes are dropped as well --
        they exist so "per transaction" matches "per-transaction" during
        binding, and repeating them ("per-transaction transaction limit") only
        makes the name harder to read. Falls back to the raw subject in the
        degenerate case where nothing distinctive was left.
        """
        distinctive = self.distinctive or self.subject
        words: list[str] = []
        for token in self.order:
            if token not in distinctive or token in words:
                continue
            if any("-" in seen and token in seen.split("-") for seen in words):
                continue
            words.append(token)
        return " ".join(words) if words else " ".join(sorted(distinctive))

    def is_legal(self, value: float) -> bool:
        if not self.low <= value <= self.high:
            return False
        return not (self.increment and value % self.increment)


def _number(raw: str) -> float:
    return float(raw.replace(",", ""))


def _ordered_tokens(text: str) -> list[str]:
    """Content words of ``text`` in order, with hyphenated forms also split.

    A split fragment follows its parent immediately, which is what lets
    ``label`` recognise one and drop it.
    """
    out: list[str] = []
    for word in _WORD_RE.findall((text or "").lower()):
        if len(word) < 3 or word in _SUBJECT_STOPWORDS:
            continue
        if word not in out:
            out.append(word)
        if "-" not in word:
            continue
        for part in word.split("-"):
            if len(part) < 3 or part in _SUBJECT_STOPWORDS or part in out:
                continue
            out.append(part)
    return out


def _tokens(text: str) -> set[str]:
    """Content words of ``text``, with hyphenated forms also split."""
    return set(_ordered_tokens(text))


def parse_bounds(grounding_text: str) -> list[_Bound]:
    """Numeric ranges stated in the SOURCE, each bound to its subject words.

    Whitespace is flattened before clauses are split: an acceptance criterion
    wraps across physical lines, and splitting on the newline separated
    "between SAR 100 and SAR 20,000" from the "in increments of SAR 100" that
    qualifies it -- which turned a correctly-rejected SAR 1,050 into a false
    positive. Never raises.
    """
    bounds: list[_Bound] = []
    try:
        flattened = " ".join((grounding_text or "").split())
        for clause in re.split(r"[.;]", flattened):
            match = _RANGE_RE.search(clause)
            if not match:
                continue
            low, high = _number(match.group(1)), _number(match.group(2))
            if low >= high:
                continue
            ordered = _ordered_tokens(clause[: match.start()])
            subject = set(ordered)
            if not subject:
                continue
            increment = _INCREMENT_RE.search(clause)
            bounds.append(
                _Bound(
                    low=low,
                    high=high,
                    increment=_number(increment.group(1)) if increment else None,
                    subject=frozenset(subject),
                    distinctive=frozenset(subject),
                    order=tuple(ordered),
                )
            )
        # A token two ranges share cannot tell them apart, so it is removed
        # from both. What is left ({monthly, spending, cap} against
        # {per-transaction, transaction, limit}) is what binding runs on.
        resolved: list[_Bound] = []
        for index, bound in enumerate(bounds):
            others: set[str] = set()
            for other_index, other in enumerate(bounds):
                if other_index != index:
                    others |= other.subject
            resolved.append(
                _Bound(
                    low=bound.low,
                    high=bound.high,
                    increment=bound.increment,
                    subject=bound.subject,
                    distinctive=frozenset(bound.subject - others),
                    order=bound.order,
                )
            )
        return resolved
    except Exception:
        logger.exception("parse_bounds failed - returning what was parsed")
        return []


def entered_value(action: str) -> float | None:
    """The number this action types into a field, or None when unreadable."""
    try:
        verb = _ENTER_VERB_RE.search(action or "")
        if not verb:
            return None
        tail = action[verb.end() : verb.end() + _VALUE_WINDOW]
        candidates: list[tuple[int, float]] = []
        quoted = _QUOTED_RE.search(tail)
        if quoted:
            literal = quoted.group(1).strip()
            if _PURE_NUMERIC_RE.match(literal):
                candidates.append(
                    (quoted.start(), _number(re.sub(r"[A-Za-z\s]", "", literal)))
                )
        currency = _CURRENCY_RE.search(tail)
        if currency:
            candidates.append((currency.start(), _number(currency.group(1))))
        if not candidates:
            return None
        return min(candidates)[1]
    except Exception:
        return None


def _bind(step_tokens: set[str], bounds: list[_Bound]) -> _Bound | None:
    """The one range this step is unambiguously about, or None."""
    if not bounds:
        return None
    scored = [(len(bound.distinctive & step_tokens), bound) for bound in bounds]
    best = max(score for score, _ in scored)
    if best < _MIN_SUBJECT_OVERLAP:
        return None
    if sum(1 for score, _ in scored if score == best) > 1:
        return None
    return next(bound for score, bound in scored if score == best)


def find_bound_contradictions(
    cases: list[TestCase], grounding_text: str
) -> list[BoundFinding]:
    """Steps whose entered value contradicts a numeric bound in the source.

    Returns [] when the source states no range, when nothing binds, or on any
    internal error. Never raises.
    """
    findings: list[BoundFinding] = []
    try:
        bounds = parse_bounds(grounding_text)
        if not bounds:
            return []
        for case in cases or []:
            case_tokens = _tokens(getattr(case, "title", "") or "")
            for datum in getattr(case, "test_data", None) or []:
                case_tokens |= _tokens(str(getattr(datum, "field", "") or ""))
            for step in getattr(case, "steps", None) or []:
                action = getattr(step, "action", "") or ""
                expected = getattr(step, "expected_result", "") or ""
                value = entered_value(action)
                if value is None:
                    continue
                bound = _bind(case_tokens | _tokens(action), bounds)
                if bound is None:
                    continue
                legal = bound.is_legal(value)
                rejected = bool(_REJECT_RE.search(expected))
                accepted = bool(_ACCEPT_RE.search(expected))
                if rejected == accepted:
                    # Both readings, or neither -- the expected result does not
                    # commit to an outcome, so there is nothing to contradict.
                    continue
                if legal and rejected:
                    kind = "legal-rejected"
                elif not legal and accepted:
                    kind = "illegal-accepted"
                else:
                    continue
                findings.append(
                    BoundFinding(
                        tc_id=getattr(case, "tc_id", "") or "",
                        step_number=int(getattr(step, "step_number", 0) or 0),
                        value=value,
                        subject=bound.label,
                        low=bound.low,
                        high=bound.high,
                        kind=kind,
                    )
                )
    except Exception:
        logger.exception("find_bound_contradictions failed - returning what was found")
    return findings
