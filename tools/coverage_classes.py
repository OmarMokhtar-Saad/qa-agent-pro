"""Suite-level coverage-class detector.

Every other detector in this tree is PER-CASE ("is this oracle falsifiable?",
"is this first step findable?"). This one asks a SUITE-level question -- "does
any case cover class X at all?" -- and absence checks fail in the direction that
hurts: a case that merely MENTIONS push and failure scores as covered while
asserting only the success path.

So a class is only ``qualifying`` when ONE case satisfies BOTH a subject matcher
and a separate adversity matcher, and the adversity must appear in a step action
or an expected result -- NEVER in the title alone, because titles are where
aspirational wording lives. Both counts are reported: "3 push cases found, 0
qualify" is the useful tester-facing sentence AND the proof the matcher is not
inert.

Calibrated against suite 1ed83399b4b84831b79ead7936235989 (96 cases, hand-read),
where the ground truth is: classes 1-7 absent, 8 and 9 present at exactly one
case each. tests/test_coverage_classes.py pins that ground truth plus a positive
control per class, because a detector that matches NOTHING would also reproduce
the "all nine missing" headline.

A class the suite never MENTIONS is not reported at all: nothing here knows
what feature the suite covers, so an unmentioned class is as likely to be out
of scope as missing. See the scope gate in ``find_missing_coverage_classes``
for what that costs on the calibration suite.

ADVISORY only, like every detector beside it: it never rejects a case, never
triggers a regeneration, and so cannot inflate step counts.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


@dataclass(frozen=True)
class CoverageClass:
    """One class of test a senior manual tester expects to find."""

    class_id: str
    label: str
    subject: re.Pattern
    adversity: re.Pattern | None
    floor: int = 1
    unspecified: bool = False


@dataclass
class ClassFinding:
    """What the suite actually contains for one class."""

    class_id: str
    label: str
    floor: int
    subject_hits: list[str] = field(default_factory=list)
    qualifying: list[str] = field(default_factory=list)
    unspecified: bool = False


# The classes are written GENERICALLY on purpose. They were derived from nine
# gaps found in one card-controls suite, but a matcher that only fires on cards
# is a matcher that reports "all clear" on every other feature. Each one asks a
# question that transfers: does a promised side effect get tested when it FAILS,
# is a mutating request ever submitted twice, is the pinned environment ever
# varied.
COVERAGE_CLASSES: list[CoverageClass] = [
    CoverageClass(
        class_id="push-failure",
        label="notification / side-effect delivery failure",
        subject=_rx(r"push|notification"),
        # Delivery-scoped ON PURPOSE. A bare negation would qualify a
        # payload-content case such as the baseline's TC-060 ("no full PAN, CVV,
        # or password in payload"), which asserts the SUCCESS path -- the exact
        # false-covered trap this detector exists to avoid.
        adversity=_rx(
            r"device\s+(?:is\s+)?(?:un|not\s+)registered"
            r"|no\s+registered\s+device"
            r"|notifications?\s+(?:are\s+)?(?:disabled|turned\s+off|blocked|denied|off\s+at)"
            r"|permissions?\s+(?:denied|revoked|disabled|off)"
            r"|deliver(?:y|ing)\s+(?:fails?|failed|failure)"
            r"|fails?\s+to\s+(?:send|deliver|arrive)"
            r"|not\s+(?:sent|delivered|received)"
            r"|never\s+(?:arrives|received|delivered)"
            r"|no\s+(?:push\s+notification|push|notification)s?\s+"
            r"(?:is\s+|was\s+|are\s+|were\s+)?(?:sent|received|delivered|arriv)"
            r"|push\s+(?:service\s+)?(?:is\s+)?(?:down|unavailable|outage)"
        ),
    ),
    CoverageClass(
        class_id="inflight-concurrency",
        label="an action landing while a related operation is in flight",
        # This subject is BROAD, it was measured, and it stays. Across the 65
        # real suites on disk it puts the class in scope in 54 and reports it
        # in 48 -- and the reflex that says "a bullet firing on three suites
        # in four is noise" does not survive the comparison: the oldest and
        # most-trusted bullet in this block, the nondeterministic-oracle one,
        # fires on 50 of the same 65. 73% is in family here, not an outlier,
        # and a firing suite gets a median of two class lines.
        #
        # Narrowing was prototyped, not argued about. Requiring the verb to
        # take one of the class's objects ("freeze the card") cuts baseline
        # mentions from 50 to 9, and adding the imperative UI form ("tap
        # Freeze") only reaches 15. Both still REPORT the class on the
        # baseline, so the tester-facing outcome is identical either way --
        # the only thing narrowing moves is the mention count, and it moves it
        # wrongly: alongside the genuine state-and-outcome noise it is meant to
        # drop ("disabled channel", "access blocked") it also drops TC-001,
        # TC-007 and TC-008, which are real freeze and unfreeze ACTIONS phrased
        # in ways no determiner list anticipated. That trades a denominator
        # that over-counts for one that under-counts, which is worse, because
        # over-counting is visible to the tester and under-counting is not.
        subject=_rx(r"freez|frozen|block|suspend|cancel|disabl"),
        adversity=_rx(
            r"in[-\s]?flight"
            r"|in\s+progress"
            r"|already\s+(?:started|initiated|underway|begun)"
            r"|mid[-\s]?(?:transaction|authoris|authoriz|payment|flow)"
            r"|pending\s+authoris|pending\s+authoriz"
            r"|during\s+(?:an?\s+)?(?:authoris|authoriz|in-flight|ongoing)"
            r"|while\s+(?:an?\s+)?(?:authoris|authoriz|transaction|payment)\s+\w*\s*is"
            r"|concurrent(?:ly)?\s+(?:with\s+)?(?:an?\s+)?(?:authoris|authoriz|transaction)"
        ),
    ),
    CoverageClass(
        class_id="retry-idempotency",
        label="idempotency of a retried or double-submitted change",
        subject=_rx(
            r"retry|retried|retries|resubmit|re-submit|submitted?\s+twice"
            r"|\btwice\b|duplicate\s+(?:request|submission|call)"
            r"|same\s+request\s+again"
            r"|double[-\s]?(?:tap|submit|click|press)|replay"
        ),
        # The assertion must be about CARDINALITY. The baseline's TC-051 retries
        # a limit change and asserts only "Limit saved as SAR 4,000" -- a near
        # miss that must stay a subject hit and never become a qualifier.
        adversity=_rx(
            r"(?:exactly|only)\s+one"
            r"|single\s+(?:entry|row|record|audit)"
            r"|no\s+duplicate|not\s+duplicated|without\s+duplicat"
            r"|duplicate\s+(?:entry|entries|row|rows|record)"
            r"|two\s+(?:entries|rows|records)"
            r"|idempoten"
            r"|one\s+audit\s+(?:entry|row)"
        ),
    ),
    CoverageClass(
        class_id="foreign-environment",
        label="a pinned clock / timezone boundary seen from elsewhere",
        # Period-scoped so that audit cases carrying a "UTC timestamp" do not
        # match the SUBJECT at all, and therefore cannot be qualified by the
        # timezone token in the adversity list.
        subject=_rx(
            r"month(?:ly)?\b(?:[^.]{0,80})(?:reset|rollover|roll\s+over|boundary|counter|cap)"
            r"|(?:reset|rollover|boundary)(?:[^.]{0,80})month"
            r"|daily\s+(?:reset|cut[-\s]?off)|midnight"
        ),
        adversity=_rx(
            r"device\s+(?:time\s?zone|tz)"
            r"|\b(?:UTC|GMT|PST|PDT|EST|EDT|CET|CEST|IST|JST)\b"
            r"|different\s+time\s?zone|another\s+time\s?zone|non[-\s]?Riyadh"
            r"|abroad|travel|overseas"
        ),
    ),
    CoverageClass(
        class_id="reversal-accounting",
        label="refunds / reversals against a running total",
        subject=_rx(r"refund|reversal|reversed|chargeback|credited\s+back"),
        adversity=_rx(r"month(?:ly)?|counter|cap|spent|total|balance"),
        unspecified=True,
    ),
    CoverageClass(
        class_id="pending-holds",
        label="pre-authorisation holds against a cap",
        subject=_rx(
            r"pre[-\s]?auth|authorisation\s+hold|authorization\s+hold"
            r"|\bhold\b(?:[^.]{0,40})(?:amount|cap|counter|fuel|hotel)"
            r"|fuel\s+(?:pump|station)|hotel\s+(?:booking|check[-\s]?in)"
        ),
        adversity=_rx(r"month(?:ly)?|counter|cap|spent|limit|total"),
        unspecified=True,
    ),
    CoverageClass(
        class_id="currency-conversion",
        label="currency conversion against a monetary limit",
        subject=_rx(
            r"conver(?:t|sion|ted)|exchange\s+rate|\bFX\b|foreign\s+currency"
            r"|\b(?:EUR|USD|GBP|AED)\b"
        ),
        adversity=_rx(
            r"per[-\s]?transaction\s+limit"
            r"|(?:before|after|pre|post)[-\s]?conversion"
            r"|settled?\s+amount|billing\s+amount"
        ),
        unspecified=True,
    ),
    CoverageClass(
        class_id="accessibility-depth",
        label="accessibility depth",
        subject=_rx(
            r"accessib|screen\s+reader|voice\s?over|talkback|aria\b|wcag"
            r"|contrast|focus\s+order|dynamic\s+type|font\s+size|text\s+siz"
            r"|tab\s+order|touch\s+target"
        ),
        # A DEPTH class, not an adversity class: the question is not "is there a
        # failure path" but "is there more than one token case", so presence is
        # the measure and the FLOOR is the bar.
        adversity=None,
        floor=2,
    ),
    CoverageClass(
        class_id="localization-depth",
        label="localization depth",
        subject=_rx(
            r"localis|localiz|arabic|\bRTL\b|right[-\s]?to[-\s]?left"
            r"|locale|translat|language|bidi"
        ),
        adversity=None,
        floor=2,
    ),
]


def _case_texts(tc: object) -> tuple[str, str]:
    """Return ``(full_text, body_text)``.

    ``body_text`` deliberately EXCLUDES the title: adversity is matched against
    it, so an aspirational title alone can never qualify a class.
    """
    title = str(getattr(tc, "title", "") or "")
    parts: list[str] = []
    for step in getattr(tc, "steps", None) or []:
        action = getattr(step, "action", None)
        expected = getattr(step, "expected_result", None)
        if action is None and isinstance(step, dict):
            action = step.get("action")
            expected = step.get("expected_result")
        parts.append(str(action or ""))
        parts.append(str(expected or ""))
    parts.append(str(getattr(tc, "preconditions", "") or ""))
    body = "\n".join(parts)
    return (title + "\n" + body), body


def find_missing_coverage_classes(cases: list) -> list[ClassFinding]:
    """Every class whose qualifying-case count is below its floor.

    Returns them in COVERAGE_CLASSES order. Never raises -- on an internal
    failure it returns whatever it had, exactly like the detectors beside it.
    """
    out: list[ClassFinding] = []
    try:
        prepared: list[tuple[str, str, str]] = []
        for tc in cases or []:
            tc_id = str(getattr(tc, "tc_id", "") or "")
            full, body = _case_texts(tc)
            prepared.append((tc_id, full, body))
        for spec in COVERAGE_CLASSES:
            finding = ClassFinding(
                class_id=spec.class_id,
                label=spec.label,
                floor=spec.floor,
                unspecified=spec.unspecified,
            )
            for tc_id, full, body in prepared:
                if not spec.subject.search(full):
                    continue
                finding.subject_hits.append(tc_id)
                if spec.adversity is None or spec.adversity.search(body):
                    finding.qualifying.append(tc_id)
            # A class with ZERO mentions is not reported, and this is the
            # scope gate the whole detector rests on. Nothing here knows what
            # feature the suite is about, so without it a cancel-order suite
            # is told it is missing currency conversion and pre-auth holds --
            # nine fixed bullets on every suite ever generated, which both
            # breaks the '' clean-suite contract of the section above and
            # spends finalize-reply budget on advice that is content-free:
            # "0 of 1 case(s), from 0 that mention the subject" repeats the
            # class list back at the tester.
            #
            # The cost is stated rather than hidden: on the calibration suite
            # this reports 7 of the 9 hand-read gaps, not 9. The two dropped
            # (refunds/reversals, pre-authorisation holds) are exactly the
            # ones whose subject appears in NEITHER the suite nor its source,
            # so there is no evidence they are in scope -- and the go-ahead
            # already routes those to an exploratory charter or an ambiguity
            # flag rather than to a generation nudge.
            if finding.subject_hits and len(finding.qualifying) < spec.floor:
                out.append(finding)
    except Exception:
        logger.exception(
            "find_missing_coverage_classes failed - returning what was found"
        )
    return out
