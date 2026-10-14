"""Requirement-unit caps and vocabulary: the one home of every scalar constant."""

from __future__ import annotations

# Bounds. A description is untrusted external content; these keep a runaway or
# hostile table from producing an unbounded unit list.
_MAX_UNITS = 200
_MAX_UNIT_CHARS = 400
_MIN_UNIT_CHARS = 8
# Caps on the INTERMEDIATE parse, not just on the emitted units. Without these,
# a description carrying thousands of fake `**DF9999**` markers or table rows is
# parsed in full before _MAX_UNITS truncates anything -- and enumerations() /
# source_ambiguity_issues() call parse_data_field_tables directly, so they would
# inherit the unbounded intermediate list. Parsing stays linear either way (no
# ReDoS), but the bound should be structural for untrusted ticket content.
_MAX_SOURCE_LINES = 2000
# How many continuation lines a single unterminated table row may absorb. Two
# covers a hard-wrapped cell; more than that and the "row" is really unrelated
# content being spliced together (see _join_wrapped_rows).
_MAX_WRAP_ABSORB = 2
_MAX_TABLES = 40
_MAX_ROWS_PER_TABLE = 120
_MAX_UC_ROWS = 200

# Row labels in the use-case table, mapped to the unit kind they produce. Keys
# are matched case-insensitively after stripping markdown emphasis/whitespace.
_UC_LABEL_KINDS: dict[str, str] = {
    "pre-condition": "precondition",
    "precondition": "precondition",
    "pre conditions": "precondition",
    "post-condition": "postcondition",
    "postcondition": "postcondition",
    "basic flow": "basic_flow",
    "main flow": "basic_flow",
    "alternative flow": "alternative_flow",
    "alternate flow": "alternative_flow",
    "exception flow": "alternative_flow",
    "business rules": "business_rule",
    "business rule": "business_rule",
}

# Prefix for each kind's unit id.
_KIND_PREFIX: dict[str, str] = {
    "precondition": "PRE",
    "postcondition": "POST",
    "basic_flow": "BF",
    "alternative_flow": "AF",
    "business_rule": "BR",
    "data_field": "DF",
}

# Words that may precede "user"/"system" WITHOUT starting a new flow step --
# "the user", "directs user to", "if the user". Without this, the Alternative
# Flow "In Step 2, If the user clicks on Keep the order System directs user to
# the same order page" shattered into four fragments instead of one unit.
_NON_ACTOR_PRECEDERS = {
    "the",
    "a",
    "an",
    "to",
    "by",
    "for",
    "of",
    "if",
    "and",
    "or",
    "that",
    "which",
    "directs",
    "notifies",
    "informs",
    "allows",
    "shows",
    "asks",
    "prompts",
    "lets",
    "redirects",
    "returns",
    "sends",
    "tells",
    "warns",
    "same",
    "each",
    "every",
    "another",
    "this",
}
_ACTORS = {"user", "system", "actor"}

# Field names whose value is expected to come from a DF enumeration. Kept small
# and explicit: a broad match would flag legitimate free-text data.
_ENUM_FIELD_HINTS = (
    "reason",
    "cancel_reason",
    "cancelation_reason",
    "cancellation_reason",
)
# Field-name fragments that mean the value is a QUANTITY or a selector, not a
# label from the enumeration -- `reason_checkbox_count: 2` matched the "reason"
# hint and was flagged as an undefined option before this filter existed.
_NON_LABEL_FIELD_FRAGMENTS = (
    "count",
    "length",
    "len",
    "index",
    "num",
    "qty",
    "quantity",
    "total",
    "size",
    "id",
    "position",
    "order_number",
)

# Requirement kinds worth reporting as unaddressed. Flow steps and data-field
# rows are deliberately EXCLUDED: a single case legitimately covers several steps
# at once, so listing them produces noise rather than a gap. Business rules and
# alternative flows are the opposite -- each is a distinct branch a suite can
# simply forget, and BR-1 ("not all products have cancelation service") is
# exactly the kind of high-risk rule that went untested on the observed run.
_COVERAGE_KINDS = frozenset({"business_rule", "alternative_flow"})
# Suppress the whole report if it would flag more than this share of the eligible
# units: that means the suite and the ticket use different vocabulary, not that
# the requirements are untested. Same reasoning as ac_anchor's _MAX_FLAG_RATIO.
_MAX_UNCOVERED_RATIO = 0.75
