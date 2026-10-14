"""Coverage class table, part A."""

from __future__ import annotations

from tools.ccl_types import CoverageClass, _rx

# The classes are written GENERICALLY on purpose. They were derived from nine
# gaps found in one card-controls suite, but a matcher that only fires on cards
# is a matcher that reports "all clear" on every other feature. Each one asks a
# question that transfers: does a promised side effect get tested when it FAILS,
# is a mutating request ever submitted twice, is the pinned environment ever
# varied.
CLASSES_A: list[CoverageClass] = [
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
]
