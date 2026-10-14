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

import logging  # noqa: F401
import re  # noqa: F401
from collections import defaultdict  # noqa: F401

from tools.bound_consistency import find_bound_contradictions  # noqa: F401
from tools.coverage_classes import find_missing_coverage_classes  # noqa: F401
from tools.models import TestCase  # noqa: F401
from tools.oracle_grounding import find_ungrounded_ui_strings  # noqa: F401
from tools.suite_consistency_budget import (  # noqa: F401
    _bound_advisory_block,
)
from tools.suite_consistency_bullets import (  # noqa: F401
    _add_conditional_bullet,
    _add_conflict_bullet,
    _add_oracle_bullet,
)
from tools.suite_consistency_bullets_src import (  # noqa: F401
    _add_bounds_bullet,
    _add_classes_bullet,
    _add_ungrounded_bullet,
    _amount,
)
from tools.suite_consistency_conflict import (  # noqa: F401
    _ids_sharing_subject,
    find_contradictory_state_assumptions,
)
from tools.suite_consistency_limits import (  # noqa: F401
    _ADV_P_CROSS_CASE,
    _ADV_P_SUGGESTION,
    _ADV_P_UNEXECUTABLE,
    _ADV_P_UNJUDGEABLE,
    _BLOCK_CAP,
    _MAX_REPORTED,
    _MIN_ROWS_SHOWN,
    _MORE_ROW_RE,
    _ROW_CAP_LADDER,
    _ROW_PREFIX,
    _row_cap_ladder,
)
from tools.suite_consistency_oracle import (  # noqa: F401
    _CONDITIONAL_ACTION_RE,
    _is_nondeterministic,
    find_conditional_actions,
    find_nondeterministic_oracles,
)
from tools.suite_consistency_pat_disj import (  # noqa: F401
    _DISJUNCTION_PATTERNS,
    _EQUIVALENT_RENDERING_RE,
    _ESCAPE_HATCH_PATTERNS,
    _NEGATED_DISJUNCTION_RE,
)
from tools.suite_consistency_pat_oracle import (  # noqa: F401
    _LOCALE_RENDERING,
    _NEGATION_WORDS,
    _NONDETERMINISTIC_PATTERNS,
)
from tools.suite_consistency_polarity import (  # noqa: F401
    _expected_polarity,
    _polarity_of,
    _subject_terms,
)
from tools.suite_consistency_section import (  # noqa: F401
    consistency_warning_section,
)
from tools.suite_consistency_state_rx import (  # noqa: F401
    _ACTION_AVAILABLE_RE,
    _ACTION_BLOCKED_RE,
    _INLINE_PAIR_RE,
    _STATE_FIELD_RE,
    _SUBJECT_STOPWORDS,
    _SUBJECT_WORD_RE,
    _TERMINAL_STATE_TOKENS,
)
from tools.suite_consistency_state_seed import (  # noqa: F401
    _is_state_value,
    _seeded_states,
)
from tools.suite_consistency_trim import (  # noqa: F401
    _advisory_omission_marker,
    _drop_bullets_to_fit,
    _shrink_rows_to_fit,
    _trim_bullet_rows,
)

logger = logging.getLogger("tools.suite_consistency")
