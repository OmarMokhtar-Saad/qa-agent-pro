"""Quoted UI copy asserted as an oracle that the source never promised.

``tools/suite_consistency`` judges a suite against ITSELF -- does an expected
result accept two outcomes, do two cases contradict each other about a seeded
state. This module judges a suite against its SOURCE, and that is why it lives
here rather than there: all three finders in ``suite_consistency`` are purely
lexical over ``list[TestCase]`` and take no second input, while the one below
cannot answer its question without the grounding text.

The defect it reports, from the live run of 2026-08-16 (suite 1ed83399): the
generator wrote ``the app shows 'Your card has been frozen'`` when the feature
description says only that the card status changes to Frozen. The string is
invented. A manual tester compares the product's real copy against it, sees a
difference, and files a defect against a promise nobody made -- so every
invented string is a false-failure generator. It is the expensive kind of
error, because it looks like rigour.

PRECONDITION -- READ THIS BEFORE WIDENING THE SCAN. It reads ``expected_result``
ONLY, and that is not a performance choice. Step ACTIONS and ``test_data`` are
where a suite legitimately carries hostile or synthetic literals it invented on
purpose: ``'<script>alert(1)</script>'``, ``'100; DROP TABLE cards;--'``,
``'WrongPass1'``, ``'abc'``. None of them is a promise about the product, and
every one of them is ungrounded by construction, so a scan that reaches them
reports the suite's deliberate payloads as hallucinated copy. Verified against
the 2026-08-16 run: no injection payload appears as a quoted span in any
expected result. ``test_a_quotable_payload_in_an_action_is_never_reported`` pins it, and pins
it with a span that CLEARS every span filter (alphanumeric at both ends, two
alpha tokens), because the obvious fixture does not:
``<script>alert(1)</script>`` is rejected by the leading-alnum rule whatever
the scan reads, so a test built on it stays green with the scoping deleted
and pins nothing at all.

That pin covers the ACTION path ONLY, and says so deliberately. A span quoted
in ``test_data`` or in a ``TestDataItem.example_value`` is a substring of the
case's own data text (``step.test_data`` + ``example_value`` -- NOT ``notes``
or ``field``, which ``_case_data_text`` does not collect), so the
self-reference filter would suppress it even if the scan reached those two
fields -- but only USUALLY, not always: ``_case_data_text``'s ``except``
returns the parts collected so far, so once one field raises, a later field's
span is unprotected. The ACTION path is the one hazard with no second line of
defence, which is why it is the one under test.
Widening the scan is a design change that has to re-answer this question,
not a one-line edit.

Deterministic, bounded, model-free, and -- like every other module in
``tools/`` -- it never raises to callers.
"""

from __future__ import annotations

import logging
import re

from tools.models import TestCase

logger = logging.getLogger(__name__)

# Below this, the "grounding text" is not a description at all -- it is a bare
# URL a tester pasted, or a ticket description that failed to resolve. Reporting
# nothing is the correct answer when there is nothing to check against.
#
# The floor's VALUE is not load-bearing; the SEPARATION behind it is. Measured
# on data/suites.db (63 suites): 51 suites have a feature_text of 147 chars or
# fewer, 12 have 528 or more, and NOTHING sits between -- a 381-char empty band,
# so any constant from roughly 150 to 525 behaves identically on this corpus.
#
# What that measurement does NOT cover, stated because the number looks more
# authoritative than it is: the check grounds on
# `prepared.target_description`, which is `feature_text` only on the PASTED
# path. On the Jira path it is the FETCHED description, which is never persisted
# -- `suites.feature_text` there holds the tester's prompt (e.g. "create test
# cases for this page https://…", 81 chars). So for the 34 URL-sourced suites in
# that corpus the measurement above describes a URL, not the text this check
# will actually read, and the clean separation is demonstrated for the 29 pasted
# suites ONLY. The Jira path is UNMEASURED in both directions.
#
# Failure direction, also undisclosed by the "2,587 ungated vs 341" framing: a
# grounding text below the floor is SKIPPED, so a short-but-real description of
# 148-199 chars reports nothing at all. That is the safe direction (silence, not
# a false-failure storm) and it is a coverage gap, not a guard.
#
# Ungated, the 51 short-grounding suites would report 2587 findings between
# them -- every quoted string in every case, because nothing can be grounded
# against a URL -- against 341 for the 12 with a real description. (An earlier
# draft said 2928 here: that is 2587 + 341, the whole corpus, stated as the
# short-grounding part of it.)
_MIN_GROUNDING_CHARS = 200

# A quoted span longer than this is a paragraph, not a UI string.
_MIN_SPAN_CHARS = 6
_MAX_SPAN_CHARS = 120

# How many real WORDS a span must carry before it is treated as asserted copy.
# This is the filter that keeps identifiers and bare values out: 'CH-100452'
# (a cardholder id the case itself seeds), 'SAR 5,000', '1,500', 'abc' and
# 'WrongPass1' each carry at most one, while 'Enter a valid amount' carries
# three. Its cost is a MISS on genuine one-word copy such as 'Frozen', which is
# the cheap direction for an advisory -- and every one-word span in the
# 2026-08-16 run was grounded anyway.
_MIN_ALPHA_TOKENS = 2

_ALPHA_TOKEN_RE = re.compile(r"[A-Za-z؀-ۿ]{2,}")

# Straight and curly, single and double. The generator writes straight singles;
# a pasted description routinely uses curly ones, and normalizing BOTH sides is
# what lets a curly-quoted promise ground a straight-quoted assertion.
_SPAN_LEN = f"{_MIN_SPAN_CHARS},{_MAX_SPAN_CHARS}"
_QUOTED_SPAN_RE = re.compile(
    rf"'([^'\n]{{{_SPAN_LEN}}})'"
    rf"|\"([^\"\n]{{{_SPAN_LEN}}})\""
    rf"|‘([^’\n]{{{_SPAN_LEN}}})’"
    rf"|“([^”\n]{{{_SPAN_LEN}}})”"
)

_SMART_CHARS = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        " ": " ",
    }
)

# Joins the case's OWN data fields. It has to be a character that cannot occur
# inside a quoted span, because joining with whitespace lets a match straddle
# two unrelated fields: with a space, a case whose postcondition ended
# "... disabled" sitting next to a data value "online purchases" grounded the
# invented string 'Online purchases disabled' out of two halves that were never
# adjacent. Measured on the 2026-08-16 run: three true positives lost that way.
_FIELD_SEP = "\x00"

# The only trailing punctuation a real UI string may end on. Anything else at
# either end means the "span" is prose caught between two unrelated quotes --
# the naive regex produced ', new_value ' and ' and action ' out of one
# expected result's ordinary commas, and both must stay silent.
_SENTENCE_TAIL = ".!?)"


def _normalize(text: str | None) -> str:
    """Case-folded, whitespace-collapsed, quote-normalized text.

    Newlines collapse to spaces on purpose: a source description is wrapped
    prose, so a promise the generator quotes on one line is frequently written
    across two. The cost is that a span can be grounded by source text that
    straddles a line break, and the direction of that error is SILENCE -- the
    safe direction for an advisory that must not cry wolf.
    """
    try:
        collapsed = re.sub(r"\s+", " ", (text or "").translate(_SMART_CHARS))
        return collapsed.strip().casefold()
    except Exception:
        logger.exception("_normalize failed - treating the text as empty")
        return ""


def _is_asserted_ui_string(span: str) -> bool:
    """True when a quoted span is plausibly copy the product must render."""
    if not span or not span[0].isalnum():
        return False
    if not span[-1].isalnum() and span[-1] not in _SENTENCE_TAIL:
        return False
    return len(_ALPHA_TOKEN_RE.findall(span)) >= _MIN_ALPHA_TOKENS


def _quoted_spans(text: str | None) -> list[str]:
    """Every quoted span in ``text``, in order, whichever quote style is used."""
    out: list[str] = []
    for match in _QUOTED_SPAN_RE.finditer(text or ""):
        span = next((group for group in match.groups() if group is not None), None)
        if span:
            out.append(span)
    return out


def _case_data_text(tc: TestCase) -> str:
    """The case's own DATA fields, sentinel-joined.

    A case that seeds ``cardholder_id: CH-100452`` and then asserts the audit
    row shows 'CH-100452' has invented nothing -- it is checking that its own
    input reached the database. Only the DATA fields count: preconditions,
    actions and postconditions are written by the same generator as the
    assertion, so letting them ground it would let the model confirm itself.
    """
    parts: list[str] = []
    try:
        for step in getattr(tc, "steps", None) or []:
            parts.append(_normalize(getattr(step, "test_data", None)))
        for item in getattr(tc, "test_data", None) or []:
            parts.append(_normalize(str(getattr(item, "example_value", "") or "")))
    except Exception:
        logger.exception("_case_data_text failed - using what was collected")
    return _FIELD_SEP.join(parts)


def find_ungrounded_ui_strings(
    cases: list[TestCase], grounding_text: str
) -> list[tuple[str, int, str]]:
    """(tc_id, step_number, span) for each invented UI string asserted as copy.

    ``grounding_text`` is the SOURCE the suite was generated from -- the pasted
    feature description, or a Jira ticket's own description plus the acceptance
    criteria parsed from it. It must never carry comment threads, parent-story
    background or RAG hits: those would let text nobody promised ground an
    assertion. That is the provenance rule recorded in
    ``agents/test_scenario_agent.grounding_sections``' own docstring, which
    keeps Jira COMMENT text out of ``source`` for the same reason -- a
    commenter must not get to define what the product promised.

    Returns [] when ``grounding_text`` is too short to be a description (see
    ``_MIN_GROUNDING_CHARS``) and on any internal error. Never raises.
    """
    try:
        grounding = _normalize(grounding_text)
        if len(grounding) < _MIN_GROUNDING_CHARS:
            return []
        out: list[tuple[str, int, str]] = []
        for tc in cases or []:
            own_data = _case_data_text(tc)
            for step in getattr(tc, "steps", None) or []:
                for span in _quoted_spans(getattr(step, "expected_result", None)):
                    if not _is_asserted_ui_string(span):
                        continue
                    normalized = _normalize(span)
                    if normalized in grounding or normalized in own_data:
                        continue
                    out.append(
                        (
                            getattr(tc, "tc_id", "") or "",
                            getattr(step, "step_number", 0) or 0,
                            span,
                        )
                    )
        return out
    except Exception:
        logger.exception("find_ungrounded_ui_strings failed - returning empty list")
        return []
