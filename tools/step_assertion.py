"""Expected results that restate the action instead of asserting an outcome (F4).

``bound_consistency`` asks whether a step's value contradicts a number the
source stated. ``suite_consistency`` asks whether an expected result has a
*shape* that can fail. This module asks the cheapest and most damaging question
of the three: does the expected result assert ANYTHING the action did not
already say?

A step whose expected_result reads "The step completes successfully: <action
verbatim>" cannot fail. It passes against correct software and against broken
software alike, so a tester executing it learns nothing, and a suite full of
them looks complete while measuring nothing at all.

MEASURED on the SHYJ-5692 run (the 8 round-2 category files):

===========  =====  =======  ======
category     steps  flagged  %
===========  =====  =======  ======
security        39       29    74.4
state           46       36    78.3
other six      246        0     0.0
===========  =====  =======  ======

Both signals flag the SAME 65 steps and nothing else. The threshold was chosen
from that measurement rather than guessed: every flagged step scores an overlap
ratio of **1.00**, and the highest-scoring step in any non-drifted category
scores **0.67**, so ``_MIN_ACTION_OVERLAP`` sits in an empty band. Those 65
steps came from a regeneration -- the round-1 files score zero in all eight
categories -- which is why the prompt-side fix lives in
``agents/test_scenario_agent``'s worked step example and this module only has
to catch what gets through.

Bias, deliberately, is towards silence, for the reason ``bound_consistency``
gives: flagging a correct suite is a far worse outcome than missing a bad step.
A step is flagged only when the expected result carries essentially no
information the action did not -- either lexically (it repeats the action) or
by matching one of a small closed list of phrases that assert nothing.

Scope, deliberately:

* it does NOT judge whether a real assertion is the RIGHT one -- an expected
  result that names a concrete but incorrect outcome is a grounding question,
  not this one;
* it does NOT flag a short expected result for being short. A one-clause
  assertion ("Payment screen SSB-3539 opens") is correct by design, and step
  count is not a quality proxy;
* the ``test_data`` findings below are DISCLOSED and never refuse. They are a
  readability defect, not a correctness one;
* the generic-phrase list is SMALL and its weak tier is anchored by dominance,
  because none of those three phrases occurred on the corpus at all -- that
  tier is reasoned, not measured, and is the one place in this module where a
  false positive is conceivable. A phrase that merely APPEARS inside a real
  assertion is not flagged;
* it does NOT judge a field NAME. A truncated-name rule was drafted and
  dropped for having a one-character margin -- see ``find_echoed_test_data``;
* the flagged set in ``category_flag_ratios`` is keyed on ``(tc_id,
  step_number)``, so DUPLICATE tc_ids double-count. Both finalize routes
  renumber during the merge before this module sees a case, so the ratios are
  correct there; a caller handing in raw per-category JSON straight off disk --
  which ``_get``'s dict support makes possible -- has no such guarantee and must
  renumber first.

Deterministic, bounded, model-free, and -- like every other module in
``tools/`` -- never raises to callers.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Fraction of the ACTION's content tokens the expected result must repeat before
# the expected result counts as a restatement. 0.70 is measured, not guessed:
# see the table in the module docstring -- the observed gap runs 0.67 (highest
# legitimate step) to 1.00 (every tautology), so anything in that band gives the
# same answer on the corpus and 0.70 is its midpoint-ish, safe end.
_MIN_ACTION_OVERLAP = 0.70

# Phrases that assert nothing. SPLIT into two tiers, because they are not
# equally safe to match as substrings.
#
# _NEVER_AN_ASSERTION is matched ANYWHERE in the expected result: there is no
# sentence in which "the step completes successfully" contributes a claim that
# could fail, and on the observed corpus it is always followed by a colon and
# the action verbatim.
#
# _WEAK_PHRASES must DOMINATE the expected result instead. Unanchored, they
# are a false-positive risk: "no error occurs and the balance updates to SAR
# 75.50" is a perfectly good assertion that happens to contain one of them.
# None of the three occurred at all on the SHYJ-5692 corpus, so the dominance
# rule below is reasoned rather than measured -- which is exactly why it is
# the CONSERVATIVE form. See the module docstring's stated limitation.
_NEVER_AN_ASSERTION = ("the step completes successfully",)
_WEAK_PHRASES = (
    "works as expected",
    "no error occurs",
    "as described above",
)

# How much of the expected result a _WEAK_PHRASE must account for before the
# expected result counts as nothing BUT that phrase.
_WEAK_PHRASE_DOMINANCE = 0.60

# A category is refused when at least this fraction of ITS steps are flagged.
# Per-CATEGORY rather than per-suite because that is how the defect actually
# arrives: two categories were regenerated and came back tautological while the
# other six were untouched. A suite-wide ratio would have read 65/331 = 20% and
# gated nothing. Measured margin: 74% and 78% on the two bad categories, 0% on
# all six others.
_CATEGORY_REFUSE_RATIO = 0.50

# NO truncated-field-name rule. One was drafted and DROPPED: the observed cut
# name was 40 characters and the longest intact one 39, so the rule's entire
# discriminating power was a single character -- inside its own noise floor,
# and the only threshold in this module not backed by a measured gap. It also
# double-reported a field that both echoed its name and was long. A rule that
# cannot be measured is not a weak rule, it is an unmeasured one.

_WORD_RE = re.compile(r"[A-Za-z0-9']+")
_ALNUM_RE = re.compile(r"[A-Za-z0-9]+")
_STOPWORDS = frozenset(
    "the a an of in on for and or to be is are with that this it its".split()
)


@dataclass(frozen=True)
class StepAssertionFinding:
    """One step whose expected_result asserts nothing the action did not say."""

    tc_id: str
    category: str
    step_number: int
    kind: str  # "restates-action" | "generic-phrase"
    overlap: float
    expected: str


@dataclass(frozen=True)
class DataFinding:
    """One test_data entry whose value merely restates its field name.

    Disclosed, never refused: it is a readability defect, not a correctness
    one.
    """

    tc_id: str
    field: str
    example_value: str
    kind: str  # "echoes-field-name"


def _tokens(text: str) -> set[str]:
    """Content words of *text*, lowercased, stopwords and 1-2 letter noise out."""
    return {
        word.lower().strip("'")
        for word in _WORD_RE.findall(text or "")
        if len(word) > 2 and word.lower() not in _STOPWORDS
    }


def action_overlap(action: str, expected: str) -> float:
    """Fraction of the ACTION's content tokens the expected result repeats.

    Measured over the action, not the union: an expected result that repeats the
    whole action AND adds a real assertion is fine, and normalising by the union
    would let a verbose tautology score low simply by being long.
    """
    try:
        action_tokens = _tokens(action)
        if not action_tokens:
            return 0.0
        return len(action_tokens & _tokens(expected)) / len(action_tokens)
    except Exception:
        logger.exception("action_overlap failed - treating the step as clean")
        return 0.0


def has_generic_phrase(expected: str) -> bool:
    """True when the expected result asserts nothing that could fail.

    Two tiers, deliberately: a never-an-assertion phrase counts wherever it
    appears, while a merely weak phrase must account for most of the text --
    otherwise "no error occurs and the balance updates to SAR 75.50", which is
    a real assertion, would be flagged.
    """
    try:
        lowered = " ".join((expected or "").lower().split())
        if not lowered:
            return False
        if any(phrase in lowered for phrase in _NEVER_AN_ASSERTION):
            return True
        for phrase in _WEAK_PHRASES:
            if phrase in lowered and len(phrase) / len(lowered) >= (
                _WEAK_PHRASE_DOMINANCE
            ):
                return True
        return False
    except Exception:
        return False


def _get(obj: object, name: str, default: object = None) -> object:
    """Read ``name`` off a model OR a plain mapping. Never raises.

    Every reader below used ``getattr`` alone. Call sites in this tree pass
    Pydantic ``TestCase`` models, so that worked -- but every artifact ON DISK is
    JSON, and a caller handing those straight in got an empty result with no
    error and no log line, which "Returns [] on any internal error. Never raises."
    made indistinguishable from a clean suite. Accepting both shapes is cheaper
    than a warning nobody reads, and it makes the corpus files a usable fixture.
    """
    try:
        if isinstance(obj, dict):
            got = obj.get(name, default)
        else:
            got = getattr(obj, name, default)
        return default if got is None else got
    except Exception:
        return default


def find_tautological_steps(cases: list) -> list[StepAssertionFinding]:
    """Steps whose expected_result restates the action or asserts nothing.

    Returns [] on any internal error. Never raises.
    """
    findings: list[StepAssertionFinding] = []
    try:
        for case in cases or []:
            tc_id = str(_get(case, "tc_id", "") or "")
            category = str(_get(case, "category", "") or "")
            for step in _get(case, "steps", None) or []:
                action = str(_get(step, "action", "") or "")
                expected = str(_get(step, "expected_result", "") or "")
                if not action or not expected:
                    continue
                overlap = action_overlap(action, expected)
                if has_generic_phrase(expected):
                    kind = "generic-phrase"
                elif overlap >= _MIN_ACTION_OVERLAP:
                    kind = "restates-action"
                else:
                    continue
                findings.append(
                    StepAssertionFinding(
                        tc_id=tc_id,
                        category=category,
                        step_number=int(_get(step, "step_number", 0) or 0),
                        kind=kind,
                        overlap=round(overlap, 2),
                        expected=expected[:160],
                    )
                )
    except Exception:
        logger.exception("find_tautological_steps failed - returning what was found")
    return findings


def category_flag_ratios(cases: list) -> dict:
    """``{category: (flagged_steps, total_steps)}`` for every category present.

    Grouped on the case's ``category``, which on the staged per-category route is
    server-derived rather than host self-report.
    """
    totals: dict = {}
    try:
        flagged = {(f.tc_id, f.step_number) for f in find_tautological_steps(cases)}
        for case in cases or []:
            category = str(_get(case, "category", "") or "") or "(uncategorised)"
            tc_id = str(_get(case, "tc_id", "") or "")
            hit, total = totals.get(category, (0, 0))
            for step in _get(case, "steps", None) or []:
                total += 1
                number = int(_get(step, "step_number", 0) or 0)
                if (tc_id, number) in flagged:
                    hit += 1
            totals[category] = (hit, total)
    except Exception:
        logger.exception("category_flag_ratios failed - returning what was counted")
    return totals


def categories_over_threshold(cases: list) -> list:
    """``[(category, flagged, total, ratio)]`` for categories at or over the
    refusal threshold, worst first. Empty list means nothing is refused.
    """
    over: list = []
    try:
        for category, (hit, total) in category_flag_ratios(cases).items():
            if not total:
                continue
            ratio = hit / total
            if ratio >= _CATEGORY_REFUSE_RATIO:
                over.append((category, hit, total, round(ratio, 3)))
        over.sort(key=lambda row: row[3], reverse=True)
    except Exception:
        logger.exception("categories_over_threshold failed")
    return over


def find_echoed_test_data(cases: list) -> list[DataFinding]:
    """test_data entries whose value merely restates the field name.

    DISCLOSED ONLY -- this never refuses a submission.

    Measured on the SHYJ-5692 run: 42 of 133 fields echo their own name
    (``simulated_payment_gateway_failure`` -> "Simulated payment gateway
    failure"). The comparison is on ALPHANUMERIC tokens, so case, underscores
    and punctuation are ignored and a value that adds ANY real content is
    silent.
    """
    findings: list[DataFinding] = []
    try:
        for case in cases or []:
            tc_id = str(_get(case, "tc_id", "") or "")
            for datum in _get(case, "test_data", None) or []:
                field = str(_get(datum, "field", "") or "")
                value = str(_get(datum, "example_value", "") or "")
                if not field:
                    continue
                field_tokens = [w.lower() for w in _ALNUM_RE.findall(field)]
                value_tokens = [w.lower() for w in _ALNUM_RE.findall(value)]
                if field_tokens and field_tokens == value_tokens:
                    findings.append(
                        DataFinding(
                            tc_id=tc_id,
                            field=field[:80],
                            example_value=value[:80],
                            kind="echoes-field-name",
                        )
                    )
    except Exception:
        logger.exception("find_echoed_test_data failed - returning what was found")
    return findings


def step_assertion_detail(findings: list) -> str:
    """Tester-facing prose for the Generation Notes sheet and the submit reply.

    Says what was measured and what it means for the suite, in the same register
    as the other Generation Notes entries: a shortfall stated plainly, with no
    instruction to the reader about what to do next.
    """
    if not findings:
        return ""
    by_category: dict = {}
    for finding in findings:
        by_category.setdefault(finding.category or "(uncategorised)", []).append(
            finding
        )
    lines = [
        f"{len(findings)} step(s) have an expected_result that asserts nothing "
        "the action did not already state -- typically a restatement of the "
        "action itself. Such a step passes against correct software AND against "
        "broken software, so executing it measures nothing.",
        "",
    ]
    for category in sorted(by_category):
        rows = by_category[category]
        sample = rows[0]
        lines.append(
            f"- {category}: {len(rows)} step(s), e.g. {sample.tc_id} "
            f'step {sample.step_number} -- "{sample.expected[:100]}"'
        )
    return "\n".join(lines)


def test_data_detail(findings: list) -> str:
    """Tester-facing prose for the test_data findings. Disclosure only."""
    if not findings:
        return ""
    sample = findings[0]
    return (
        f"{len(findings)} test_data field(s) use the field NAME as the example "
        f'value (e.g. {sample.tc_id}: "{sample.field}" -> '
        f'"{sample.example_value}"). That is a label, not a value a tester can '
        "enter, so the case does not say what to type. Nothing was refused over "
        "this -- it is a readability defect, not a correctness one."
    )
