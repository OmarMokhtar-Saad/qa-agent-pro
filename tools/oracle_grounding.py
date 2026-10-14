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
import re  # noqa: F401

from tools.models import TestCase
from tools.oracle_grounding_case import case_findings
from tools.oracle_grounding_copy import (  # noqa: F401
    _ALPHA_TOKEN_RE,
    _MIN_ALPHA_TOKENS,
    _SENTENCE_TAIL,
    _is_asserted_ui_string,
    _looks_like_identifier,
)
from tools.oracle_grounding_spans import (  # noqa: F401
    _MAX_SPAN_CHARS,
    _MIN_SPAN_CHARS,
    _QUOTED_SPAN_RE,
    _SPAN_LEN,
    _quoted_spans,
)
from tools.oracle_grounding_template import (  # noqa: F401
    _MAX_TEMPLATES,
    _PLACEHOLDER_RE,
    _template_patterns,
)
from tools.oracle_grounding_text import (  # noqa: F401
    _FIELD_SEP,
    _SMART_CHARS,
    _case_data_text,
    _normalize,
)

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


# ``grounding_text`` is the SOURCE the suite was generated from -- the pasted
# feature description, or a Jira ticket's own description plus the acceptance
# criteria parsed from it. It must never carry comment threads, parent-story
# background or RAG hits: those would let text nobody promised ground an
# assertion. That is the provenance rule recorded in
# ``agents/test_scenario_agent.grounding_sections``' own docstring, which
# keeps Jira COMMENT text out of ``source`` for the same reason -- a
# commenter must not get to define what the product promised.
#
# Returns [] when ``grounding_text`` is too short to be a description (see
# ``_MIN_GROUNDING_CHARS``) and on any internal error. Never raises.
def find_ungrounded_ui_strings(
    cases: list[TestCase], grounding_text: str
) -> list[tuple[str, int, str]]:
    """(tc_id, step_number, span) for each invented UI string asserted as copy."""
    try:
        grounding = _normalize(grounding_text)
        if len(grounding) < _MIN_GROUNDING_CHARS:
            return []
        templates = _template_patterns(grounding)
        out: list[tuple[str, int, str]] = []
        for tc in cases or []:
            out.extend(case_findings(tc, grounding, templates))
        return out
    except Exception:
        logger.exception("find_ungrounded_ui_strings failed - returning empty list")
        return []
