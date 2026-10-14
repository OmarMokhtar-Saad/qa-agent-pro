"""AC-anchoring check (TICKET-7154 Fix 3).

When a source ticket carries REAL, human-authored acceptance criteria, every
generated test case should trace back to one of them. Cases that cite a
non-existent AC id (hallucinated traceability) or cite none at all are
"unanchored". This module flags them (advisory) and can optionally drop the
hallucinated ones (QA_AC_ANCHORING_ENFORCE), never emptying the suite.

Only meaningful when the ACs were parsed from the SOURCE content — synthesized
ACs are not a ground-truth anchor, so the caller passes only source ACs here.

Never raises — every helper degrades to a benign result so it can never break
generation.
"""

from __future__ import annotations

import logging
import re  # noqa: F401

from tools.acanc_anchor import (  # noqa: F401
    _real_ac_ids,
    anchoring_warning_section,
    classify_cases,
    filter_unanchored_cases,
)
from tools.acanc_scope import (  # noqa: F401
    _MAX_FLAG_RATIO,
    flag_out_of_scope_cases,
    scope_warning_section,
)
from tools.acanc_terms import (  # noqa: F401
    _SCOPE_STOPWORDS,
    _WORD_RE,
    _case_terms,
    _terms,
)
from tools.models import TestCase  # noqa: F401
from tools.rtm import AcceptanceCriterion, normalize_ac_id  # noqa: F401

logger = logging.getLogger(__name__)
