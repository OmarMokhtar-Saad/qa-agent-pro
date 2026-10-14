"""Requirement units for use-case-table stories (grounding Phase 2).

A Jira story does not have to carry an "Acceptance Criteria" heading to be fully
specified. The client's app-store tickets express their requirements as a use-case
TABLE -- UC / Description / Actor / Pre-condition / Post-condition / Basic Flow /
Alternative Flow / Business Rules -- followed by data-field tables (DF01, DF02,
...) that pin the exact on-screen strings. Nothing in that layout reaches
``rtm.parse_acceptance_criteria``, so a story like TICKET-5645 produced ZERO
anchorable requirements and every grounding gate downstream went inert: cases
asserting refunds, stock release and push notifications -- none of which the
story mentions -- had nothing to fail against.

This module turns that layout into atomic, addressable REQUIREMENT UNITS so a
generated case can cite what it validates, and so the deterministic source
checks below can report defects in the TICKET itself (duplicate rule ids,
duplicate table ids, a label repeated across two buttons) without any model
call.

Relationship to ``jira_mcp.extract_ac_from_uc_table`` (read before merging them)
--------------------------------------------------------------------------------
Two parsers read the same UC table on purpose. They are NOT duplicates and
neither supersedes the other:

* ``jira_mcp.extract_ac_from_uc_table`` emits ONE criterion per
  requirement-bearing ROW and feeds the existing acceptance-criteria / RTM path
  (``requirement_id`` tagging). Its docstring argues the case for staying coarse:
  the rows are run-on prose, so splitting them invents boundaries the ticket does
  not state.
* THIS module splits those same rows into individually addressable units
  (``BF-1`` … ``BF-6``, ``AF-n``, ``BR-n``, ``DFnn-n``) because the gates built on
  it need that granularity. The Alternative Flow of TICKET-5645 literally says "In
  Step 2", so "step 2 of the Basic Flow" has to be a citable thing before a case
  that exercises the wrong screen can be caught mechanically. It also carries
  provenance and the data-field tables, which the row-level parser does not model.

The shared piece is already unified: ``jira_mcp.usable_ac_text`` delegates to
``rtm.looks_like_requirement_text``, so the two paths cannot disagree about what
counts as requirement text. Collapsing the two PARSERS, by contrast, would cost
either the coarse-and-safe criteria the RTM path wants or the step-level ids the
gates need -- so do it only as a deliberate decision, not as a cleanup.

Design rules, same as the rest of ``tools/``:
* Never raises to callers -- every helper degrades to a benign empty result.
* No LLM call, no I/O, no internal imports beyond ``tools.models``.
* Bounded: unit count and per-unit length are capped so a hostile or runaway
  description cannot blow up the payload.
* Provenance-tagged: units parsed from the TARGET issue are ``"target"``; units
  derived from parent / sibling / linked-issue background are ``"background"``
  and must never be offered to a generator as something to cover.
"""

from __future__ import annotations

import logging
import re  # noqa: F401
from collections.abc import Callable  # noqa: F401

from tools.models import TestCase  # noqa: F401
from tools.ru_cells import (  # noqa: F401
    _clean_cell,
    _label_kind,
    _section_id_match,
    _split_row,
)
from tools.ru_consts import (  # noqa: F401
    _ACTORS,
    _COVERAGE_KINDS,
    _ENUM_FIELD_HINTS,
    _KIND_PREFIX,
    _MAX_ROWS_PER_TABLE,
    _MAX_SOURCE_LINES,
    _MAX_TABLES,
    _MAX_UC_ROWS,
    _MAX_UNCOVERED_RATIO,
    _MAX_UNIT_CHARS,
    _MAX_UNITS,
    _MAX_WRAP_ABSORB,
    _MIN_UNIT_CHARS,
    _NON_ACTOR_PRECEDERS,
    _NON_LABEL_FIELD_FRAGMENTS,
    _UC_LABEL_KINDS,
)
from tools.ru_coverage import (  # noqa: F401
    coverage_warning_section,
    enum_warning_section,
    find_unaddressed_requirements,
)
from tools.ru_enum import (  # noqa: F401
    _case_text,
    _normalize_label,
    _unknown_enum_hits,
    find_unknown_enum_values,
)
from tools.ru_flow import (  # noqa: F401
    _split_flow,
    _split_on_actors,
    _split_rules,
)
from tools.ru_model import (  # noqa: F401
    DataFieldTable,
    RequirementUnit,
    _strip_placeholder_brackets,
)
from tools.ru_parse import (  # noqa: F401
    _make_adder,
    _next_unit_id,
    _unit_cap_reached,
    assignable_unit_ids,
    parse_requirement_units,
)
from tools.ru_patterns import (  # noqa: F401
    _CUSTOM_TAG_RE,
    _EMPHASIS_RE,
    _ENUM_SPLIT_RE,
    _FREE_TEXT_EVIDENCE_RE,
    _GENERIC_POINTER_RE,
    _HTML_TAG_RE,
    _RULE_ID_RE,
    _SECTION_ID_RE,
    _STRAY_BACKSLASH_RE,
    _TABLE_ROW_RE,
    _TABLE_SEP_RE,
    _WORD_RE,
    _WS_RE,
)
from tools.ru_source import (  # noqa: F401
    _conflicting_label_issues,
    _duplicate_rule_issues,
    _duplicate_table_issues,
    enumerations,
    free_text_tables,
    source_ambiguity_issues,
)
from tools.ru_tables import (  # noqa: F401
    _add_data_field_units,
    _data_field_row,
    _df_text,
    _DfScan,
    _table_key,
    parse_data_field_tables,
)
from tools.ru_uc import (  # noqa: F401
    _add_use_case_units,
    _uc_rows,
)
from tools.ru_wrap import (  # noqa: F401
    _absorb_wrap,
    _join_step,
    _join_wrapped_rows,
)

logger = logging.getLogger(__name__)
