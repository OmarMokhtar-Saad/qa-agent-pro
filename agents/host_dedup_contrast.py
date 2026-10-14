"""The boundary-contrast filter of the duplicate shortlist (F5)."""

from __future__ import annotations

from agents.host_dedup_consts import _UNTRUSTED_ERRORS

# F5 (2026-08-30, MEASURED): across three live runs the prescreen surfaced 8
# candidate pairs and NONE was a duplicate -- every one was a deliberate
# boundary or contrast pair (min vs max, below vs above, increased vs
# decreased, production vs UAT, negative vs non-numeric). That is not bad luck:
# title-word overlap is HIGHEST precisely where two cases are opposites, which
# is the coverage a good suite must contain. The finalize reply then told the
# tester their (correct) clean review was "CONTRADICTED by the server's own
# prescreen", nudging a non-technical tester toward deleting boundary coverage.
#
# The filter is the one the section's OWN guidance already states -- "drop any
# pair that differs in boundary value" -- applied server-side instead of being
# delegated to the reader. A pair is suppressed only when EVERY token the two
# titles disagree about is a discriminator: a contrast word, or a token
# carrying a digit. Two cases that differ in a product noun are untouched.
_DUP_DISCRIMINATORS = frozenset(
    {
        "min",
        "max",
        "minimum",
        "maximum",
        "lower",
        "upper",
        "least",
        "most",
        "above",
        "below",
        "over",
        "under",
        "before",
        "after",
        "beyond",
        "within",
        "increase",
        "increased",
        "increases",
        "decrease",
        "decreased",
        "decreases",
        "more",
        "less",
        "fewer",
        "greater",
        "smaller",
        "larger",
        "longer",
        "shorter",
        "first",
        "last",
        "start",
        "end",
        "top",
        "bottom",
        "valid",
        "invalid",
        "enabled",
        "disabled",
        "allowed",
        "blocked",
        "present",
        "absent",
        "empty",
        "full",
        "positive",
        "negative",
        "numeric",
        "alphanumeric",
        "alphabetic",
        "cyrillic",
        "unicode",
        "ascii",
        "single",
        "multiple",
        "none",
        "all",
        "missing",
        "extra",
        "production",
        "prod",
        "staging",
        "uat",
        "sandbox",
        "qa",
        "dev",
        "development",
        "preprod",
        "live",
        "local",
        "penny",
        "cent",
        "zero",
        "one",
        "two",
        "non",
        "not",
        "no",
        "android",
        "ios",
        "web",
        "mobile",
        "desktop",
        "tablet",
    }
)


# True when two titles disagree ONLY about contrast WORDS.
#
# Pure and never raises. Returns False when the titles are identical -- an
# identical pair is the one shape that really IS a duplicate, and suppressing
# it would break the check this filter exists to make usable.
#
# A DIGIT-BEARING token is deliberately NOT a discriminator, though the first
# cut of this filter treated it as one. "penny below minimum" ~ "penny above
# minimum" is a boundary pair because of below/above, not because of a
# number; meanwhile 40 titles differing only by "variant 1", "variant 2", ...
# are the duplicate shape this prescreen exists to catch, and the digit rule
# silenced them (tests/test_dup_prescreen_merged_submit.py pins that fixture).
# Re-measured against the eight pairs the 2026-08-30 run reported: the word
# list alone suppresses seven of the eight.
def _is_boundary_contrast(tokens_a: frozenset, tokens_b: frozenset) -> bool:
    """True when two titles disagree ONLY about contrast WORDS. Pure; never raises."""
    try:
        diff = tokens_a.symmetric_difference(tokens_b)
        if not diff:
            return False
        return all(t in _DUP_DISCRIMINATORS for t in diff)
    except _UNTRUSTED_ERRORS:  # pragma: no cover - a filter never breaks the prescreen
        return False
