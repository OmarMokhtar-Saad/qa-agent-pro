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

MEASURED on the TICKET-5692 run (the 8 round-2 category files):

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

import logging  # noqa: F401
import re  # noqa: F401
from dataclasses import dataclass  # noqa: F401
from typing import Any  # noqa: F401

from tools.sasrt_data import find_echoed_test_data  # noqa: F401
from tools.sasrt_detail import step_assertion_detail, test_data_detail  # noqa: F401
from tools.sasrt_taut import (  # noqa: F401
    _CATEGORY_REFUSE_RATIO,
    _MIN_ACTION_OVERLAP,
    categories_over_threshold,
    category_flag_ratios,
    find_tautological_steps,
)
from tools.sasrt_text import (  # noqa: F401
    _ALNUM_RE,
    _NEVER_AN_ASSERTION,
    _STOPWORDS,
    _WEAK_PHRASE_DOMINANCE,
    _WEAK_PHRASES,
    _WORD_RE,
    _get,
    _tokens,
    action_overlap,
    has_generic_phrase,
)
from tools.sasrt_types import DataFinding, StepAssertionFinding  # noqa: F401

logger = logging.getLogger(__name__)
