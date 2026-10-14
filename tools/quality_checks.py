"""Cheap heuristic quality gate for generated test cases.

Flags vague step actions (e.g. "enter any value") and placeholder test_data
(e.g. "anything") so drift can't silently reach the exported files. This is a
bounded, regex-based heuristic — not a semantic check — kept intentionally
cheap so it can run on every category's output without an extra LLM call.
Never raises to callers.

Deliberately does NOT touch technical/security step *content* — it only flags
vague phrasing versus a literal value; a step that opens DevTools, inspects
headers, or uses a SQL/XSS payload is fine as long as the payload/value/field
is spelled out.

This module is the public facade: the implementation lives in the flat
`tools/quality_*.py` modules, and every name callers or tests reach through
here is re-exported from its home. Every module logs through the one logger
named `tools.quality_checks` (each calls `logging.getLogger` with that name).
"""

from __future__ import annotations

import logging

from tools.quality_chained_refs import (  # noqa: F401
    resolve_chained_refs_to_stable,
    restore_chained_refs_from_stable,
)
from tools.quality_controls import (  # noqa: F401
    _MAX_CONTROL_TAIL_CHARS,
    _is_modifier_of_a_control,
)
from tools.quality_data_gap import (  # noqa: F401
    DATA_GAP_MAX_ID_CHARS,
    DATA_GAP_MAX_IDS,
    DATA_GAP_ROW_COST,
)
from tools.quality_data_notes import (  # noqa: F401
    DATA_NOTES_MAX_CASES,
    data_notes_section,
)
from tools.quality_empty_data import (  # noqa: F401
    EMPTY_DATA_MIN_CASES,
    EMPTY_DATA_WARN_RATIO,
    find_empty_data_cases,
    find_empty_test_data_cases,
)
from tools.quality_findability import (  # noqa: F401
    FINDABILITY_MAX_LOGGED,
    find_unanchored_first_steps,
)
from tools.quality_module_merges import _qualifier_prefix_merges  # noqa: F401
from tools.quality_module_names import normalize_module_names  # noqa: F401
from tools.quality_report import quality_ratio, quality_warning_section  # noqa: F401
from tools.quality_screens import _first_step_is_off_app  # noqa: F401
from tools.quality_vague import (  # noqa: F401
    _has_concrete_value,
    _is_placeholder_data,
    _is_vague_expected,
    find_placeholder_data,
    find_vague_expected,
    find_vague_steps,
)

logger = logging.getLogger(__name__)
