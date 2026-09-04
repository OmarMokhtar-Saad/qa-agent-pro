"""Requirement units for use-case-table stories (grounding Phase 2).

A Jira story does not have to carry an "Acceptance Criteria" heading to be fully
specified. The client's app-store tickets express their requirements as a use-case
TABLE -- UC / Description / Actor / Pre-condition / Post-condition / Basic Flow /
Alternative Flow / Business Rules -- followed by data-field tables (DF01, DF02,
...) that pin the exact on-screen strings. Nothing in that layout reaches
``rtm.parse_acceptance_criteria``, so a story like SHYJ-5645 produced ZERO
anchorable requirements and every grounding gate downstream went inert: cases
asserting refunds, stock release and push notifications -- none of which the
story mentions -- had nothing to fail against.

This module turns that layout into atomic, addressable REQUIREMENT UNITS so a
generated case can cite what it validates, and so the deterministic source
checks below can report defects in the TICKET itself (duplicate rule ids,
duplicate table ids, a label repeated across two buttons) without any model
call.

Relationship to ``jira_mcp._extract_ac_from_uc_table`` (read before merging them)
--------------------------------------------------------------------------------
Two parsers read the same UC table on purpose. They are NOT duplicates and
neither supersedes the other:

* ``jira_mcp._extract_ac_from_uc_table`` emits ONE criterion per
  requirement-bearing ROW and feeds the existing acceptance-criteria / RTM path
  (``requirement_id`` tagging). Its docstring argues the case for staying coarse:
  the rows are run-on prose, so splitting them invents boundaries the ticket does
  not state.
* THIS module splits those same rows into individually addressable units
  (``BF-1`` … ``BF-6``, ``AF-n``, ``BR-n``, ``DFnn-n``) because the gates built on
  it need that granularity. The Alternative Flow of SHYJ-5645 literally says "In
  Step 2", so "step 2 of the Basic Flow" has to be a citable thing before a case
  that exercises the wrong screen can be caught mechanically. It also carries
  provenance and the data-field tables, which the row-level parser does not model.

The shared piece is already unified: ``jira_mcp._usable_ac_text`` delegates to
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
import re
from dataclasses import dataclass

from tools.models import TestCase

logger = logging.getLogger(__name__)

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

# A markdown pipe-table row: | cell | cell | ...
_TABLE_ROW_RE = re.compile(r"^\s*\|(?P<body>.+)\|\s*$")
# A separator row: | --- | --- |
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
# A standalone data-field / UI section marker, e.g. **DF01** or **UI#02**
#
# 2026-09-02 audit F10: every quantifier here is BOUNDED, and that is the whole
# point of the shape. The pattern used to read `^\s*\**\s*...\s*\**\s*$`, which
# put two unbounded whitespace runs either side of an unbounded optional one. A
# line of N leading spaces then cost O(N**2) just to be REFUSED -- 40,000 spaces
# measured 16 s, x4 per doubling -- and this pattern is matched once per source
# line by BOTH parse_data_field_tables and parse_requirement_units, on every
# finalize, over Jira description text nobody in this tree authored.
#
# A real marker is `**DF01**`. The bounds below are an order of magnitude more
# slack than any real one needs, so no marker that used to match stops matching,
# and the refusal is now constant-cost.
#
# `[^\S\r\n]` rather than `[ \t]`: it is `\s` MINUS the line terminators, so a
# Jira-pasted marker indented with a NON-BREAKING space still matches, exactly as
# it did under `\s`. A plain `[ \t]` was the first draft of this fix and it
# narrowed real behaviour -- \xa0 is ordinary in text that came through a
# rich-text editor. Excluding \r and \n costs nothing (these lines come from
# splitlines() and are rstrip()ped, so no terminator survives) and keeps the
# class unable to span a line.
_SECTION_ID_RE = re.compile(
    r"^[^\S\r\n]{0,20}\*{0,4}[^\S\r\n]{0,20}"
    r"(?P<id>(?:DF|UI#?)[^\S\r\n]{0,4}\d{1,6})"
    r"[^\S\r\n]{0,20}\*{0,4}[^\S\r\n]{0,20}$",
    re.I,
)
# 2026-09-02 review: a `_MAX_MARKER_LINE_CHARS = 120` bound stood here, and it
# was SEMANTICALLY unreachable -- though not free, and the round-2 review was
# right that the first wording read as "it did nothing". It was a ~230x
# CONSTANT-FACTOR shave on lines indented past 20 characters (11.2 us -> 0.05 us,
# measured) and it fired on 27,332 real lines in this repo. That constant is
# deliberately given up, because what the cap could never do is change an
# ANSWER: The bounded pattern above cannot match a string longer than
# 101 characters (20+4+20+13+20+4+20, and the longest matchable string measured
# 85), so no line the cap rejected could ever have reached the pattern anyway --
# a mutation that disabled the cap left every test green, because the pattern
# was doing the work. It is deleted rather than lowered: keeping it would tell
# the next reader that the quantifier bounds have a backstop, and they do not.
# The bounds ARE the guard, and they are pinned by
# tests/test_parser_growth_bounds.py::test_the_marker_test_cost_is_linear_in_the_width_of_ordinary_lines
# (mutation: unbound them and that test reads 3.50x per doubling).
# A declared business-rule id inside prose, e.g. **BR02**: or BR2.
_RULE_ID_RE = re.compile(r"\*{0,2}(?P<id>BR\s*\d+)\*{0,2}\s*[:.\)-]?", re.I)
# A numbered / bulleted flow, which is the conventional way to write one and is
# preferred over the actor-based fallback in _split_on_actors.
_ENUM_SPLIT_RE = re.compile(r"(?m)^\s*(?:\d+[.)\]]|[-*•])\s+")
# Markdown emphasis and Jira smart-link / emoji wrappers to strip from a cell.
_EMPHASIS_RE = re.compile(r"\*{1,3}|_{2,}|`+")
_CUSTOM_TAG_RE = re.compile(r"</?custom[^>]*>", re.I)
# Only a KNOWN html/adf tag is stripped. A generic `<[^>]+>` would eat the
# placeholder cells these tables are built from -- `<I no longer need the
# product>` is a DF01 checkbox LABEL, not markup, and swallowing it left
# `enumerations()` empty, disabling the undefined-option check entirely.
# Multi-letter tags may carry attributes; single-letter tags must be BARE. A
# permissive `<i ...>` swallowed the DF01 label `<I no longer need the product>`
# whole, which is why the English enum values went missing.
_HTML_TAG_RE = re.compile(
    r"</?(?:custom|strong|span|div|img|code|pre|table|thead|tbody|tr|td|th|"
    r"ul|ol|li|blockquote|br|hr|h[1-6]|sub|sup|em)(?:\s[^>]{0,200})?/?>"
    r"|</?(?:i|u|s|b|a|p)\s*/?>",
    re.I,
)
_STRAY_BACKSLASH_RE = re.compile(r"\\+")
_WS_RE = re.compile(r"\s+")

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
_WORD_RE = re.compile(r"\S+")


@dataclass(frozen=True)
class RequirementUnit:
    """One atomic, citable requirement.

    ``unit_id`` is what a generated test case puts in ``requirement_id``.
    ``provenance`` is ``"target"`` for the issue under test and ``"background"``
    for anything derived from parent / sibling / linked issues -- a case may
    only ever be anchored to a ``"target"`` unit.
    """

    unit_id: str
    text: str
    kind: str
    provenance: str = "target"

    @property
    def is_assignable(self) -> bool:
        """True when a test case may cite this unit as its requirement."""
        return self.provenance == "target"


def _clean_cell(raw: str) -> str:
    """Strip markdown emphasis, known HTML/ADF tags and collapse whitespace."""
    try:
        text = _CUSTOM_TAG_RE.sub(" ", raw or "")
        text = _HTML_TAG_RE.sub(" ", text)
        text = _EMPHASIS_RE.sub("", text)
        text = _STRAY_BACKSLASH_RE.sub("", text)
        return _WS_RE.sub(" ", text).strip()
    except Exception:
        logger.exception("_clean_cell failed - returning empty string")
        return ""


def _section_id_match(line: str) -> re.Match[str] | None:
    """The single entry point for the marker test.

    Retained after the length bound it used to apply was deleted as unreachable
    (see the note above ``_SECTION_ID_RE``): one call site for the pattern is
    still worth having, because the 2026-09-02 audit found this pattern
    reachable from a Jira description through TWO call sites (:269 and :491) and
    a future bound applied at one of them would be forgotten at the other.
    Never raises.
    """
    return _SECTION_ID_RE.match(line)


def _join_wrapped_rows(description: str) -> list[str]:
    """Lines of the description with wrapped table rows re-joined.

    A Jira-rendered table cell can carry a hard newline, which splits ONE row
    across two lines (`| <cell` / ` | <cell> | Checkbox |`). Parsing those
    separately shifts every later cell by one column and silently drops the DF01
    checkbox labels, so such rows are re-assembled first.

    Joining is BOUNDED and REVERSIBLE, because the naive version ("absorb lines
    until one ends in a pipe") had two failure modes, both found in review:

    * A table written WITHOUT trailing pipes -- legal GFM, and a common style --
      made every row look unterminated, so the whole description collapsed into a
      single line and unit parsing, the enumerations and the source-defect checks
      all silently returned nothing.
    * An unterminated row absorbed unrelated later rows, splicing them into one
      fabricated "requirement" and hiding the very duplicate-id defects this
      module exists to report.

    So: at most :data:`_MAX_WRAP_ABSORB` continuation lines are absorbed, a line
    that is itself a complete row or a section marker is never absorbed, and if
    the row still does not close the ORIGINAL lines are emitted unchanged. A row
    that legitimately has no trailing pipe is then parsed by
    :func:`_split_row`, which treats the trailing pipe as optional.
    """
    out: list[str] = []
    try:
        lines = (description or "").splitlines()[:_MAX_SOURCE_LINES]
        index = 0
        while index < len(lines):
            line = lines[index].rstrip()
            stripped = line.strip()
            if not (stripped.startswith("|") and not stripped.endswith("|")):
                out.append(line)
                index += 1
                continue
            # Only a LONE OPENING CELL is a wrapped row. A line with two or
            # more cells is a complete record written without a trailing pipe,
            # and treating it as unterminated is what collapsed whole
            # descriptions. The real Jira wrapping looks like
            #     | < خطأ في معلومات الطلب>
            #      | <Incorrect order information > | Checkbox |
            # so the fragment has ONE cell and its continuation legitimately
            # looks like a complete row -- which means the continuation cannot be
            # rejected for looking complete, only the fragment can be rejected
            # for looking complete.
            if len(_split_row(line)) != 1:
                out.append(line)
                index += 1
                continue
            merged = line
            consumed = 0
            closed = False
            while consumed < _MAX_WRAP_ABSORB and index + 1 + consumed < len(lines):
                nxt = lines[index + 1 + consumed].rstrip()
                # A "**DF01**"-style marker starts a new table; never absorb it.
                if _section_id_match(nxt) or not nxt.strip():
                    break
                merged = merged + " " + nxt.lstrip()
                consumed += 1
                if merged.rstrip().endswith("|"):
                    closed = True
                    break
            if closed:
                out.append(merged)
                index += 1 + consumed
            else:
                # Leave it alone -- _split_row tolerates the missing pipe.
                out.append(line)
                index += 1
    except Exception:
        logger.exception("_join_wrapped_rows failed - using raw lines")
        return (description or "").splitlines()[:_MAX_SOURCE_LINES]
    return out


def _split_row(line: str) -> list[str]:
    """Cells of a markdown pipe-table row, or [] when the line is not one.

    The trailing pipe is OPTIONAL: `| a | b | c` is as legal in GFM as
    `| a | b | c |`, and requiring it meant a table in that style produced no
    rows at all -- which silently disabled every check built on this module.
    """
    try:
        if _TABLE_SEP_RE.match(line):
            return []
        stripped = (line or "").strip()
        if not stripped.startswith("|"):
            return []
        body = stripped[1:]
        if body.endswith("|"):
            body = body[:-1]
        if not body.strip():
            return []
        return [_clean_cell(cell) for cell in body.split("|")]
    except Exception:
        return []


def _label_kind(label: str) -> str | None:
    """The unit kind a use-case table row label maps to, or None."""
    key = _clean_cell(label).lower().rstrip(":").strip()
    return _UC_LABEL_KINDS.get(key)


def _split_on_actors(text: str) -> list[str]:
    """Split a run-on flow cell where a fresh ACTOR begins a new step.

    A bare lookahead on "user"/"system" is not enough: those words also appear
    mid-clause ("the user", "directs user to"), which shredded the Alternative
    Flow into fragments. A new step therefore starts only when the actor word is
    at the very beginning or follows a word that cannot precede a subject
    (:data:`_NON_ACTOR_PRECEDERS`), or follows sentence punctuation.
    """
    try:
        words = _WORD_RE.findall(text or "")
        if not words:
            return []
        steps: list[str] = []
        current: list[str] = []
        for index, word in enumerate(words):
            bare = word.strip(".,;:!?\"'()").lower()
            starts_step = False
            if bare in _ACTORS and current:
                previous = words[index - 1].strip("\"'()").lower()
                previous_bare = previous.strip(".,;:!?")
                if previous.endswith((".", ";", ":")):
                    starts_step = True
                elif previous_bare not in _NON_ACTOR_PRECEDERS:
                    starts_step = True
            if starts_step:
                steps.append(" ".join(current).strip(" .;"))
                current = [word]
            else:
                current.append(word)
        if current:
            steps.append(" ".join(current).strip(" .;"))
        return [s for s in steps if s]
    except Exception:
        logger.exception("_split_on_actors failed - treating the cell as one step")
        return [text] if text else []


def _split_flow(cell: str) -> list[str]:
    """Split a flow cell into individual steps.

    Prefers an explicit numbered/bulleted list; falls back to splitting on
    actor-led sentence starts, which is how the use-case tables in this project
    are actually written (one run-on cell, no separators).
    """
    try:
        text = (cell or "").strip()
        if not text:
            return []
        parts = [p.strip() for p in _ENUM_SPLIT_RE.split(text) if p.strip()]
        if len(parts) > 1:
            return parts
        parts = _split_on_actors(text)
        if len(parts) > 1:
            return parts
        return [text]
    except Exception:
        logger.exception("_split_flow failed - treating the cell as one step")
        return [cell] if cell else []


def _split_rules(cell: str) -> list[tuple[str | None, str]]:
    """Split a Business Rules cell into (declared_id, text) pairs.

    The declared id is kept verbatim when the ticket states one (``BR02``) so
    duplicate-id detection can report the ticket's own numbering defect; the
    caller still assigns a unique sequential unit id.
    """
    try:
        text = (cell or "").strip()
        if not text:
            return []
        matches = list(_RULE_ID_RE.finditer(text))
        if not matches:
            return [(None, part) for part in _split_flow(text)]
        out: list[tuple[str | None, str]] = []
        for i, match in enumerate(matches):
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[start:end].strip(" .;:-")
            declared = _WS_RE.sub("", match.group("id")).upper()
            if body:
                out.append((declared, body))
        return out
    except Exception:
        logger.exception("_split_rules failed - returning no rules")
        return []


def _uc_rows(description: str) -> list[tuple[str, str]]:
    """(label, value) pairs from the leading use-case table."""
    rows: list[tuple[str, str]] = []
    try:
        for line in _join_wrapped_rows(description):
            cells = _split_row(line)
            if len(cells) >= 2 and cells[0]:
                rows.append((cells[0], " ".join(c for c in cells[1:] if c)))
            if len(rows) >= _MAX_UC_ROWS:
                logger.info("_uc_rows hit the %d-row cap - truncating", _MAX_UC_ROWS)
                break
    except Exception:
        logger.exception("_uc_rows failed - returning what was parsed")
    return rows


@dataclass(frozen=True)
class DataFieldTable:
    """One DF table: its declared id and its rows.

    ``rows`` are (content_ar, content_en, type) triples as they appear. A table
    whose header could not be understood keeps whatever cells were present, so a
    malformed table degrades to fewer units rather than to an exception.
    """

    table_id: str
    rows: tuple[tuple[str, str, str], ...]

    def enum_values(self) -> set[str]:
        """Labels of the selectable (Checkbox / Radio) rows, in BOTH languages.

        Both columns count as defined options. Keeping only the English label
        meant an Arabic-valued case was reported as selecting "an option the
        ticket does not define" while that exact label sat in the ticket's own
        Arabic column on the same row -- and bilingual cases are the norm here
        (the bilingual rule pack, removed as a setting 2026-08-14), so that
        single omission would have fired on
        most real Arabic suites.
        """
        out: set[str] = set()
        for ar, en, kind in self.rows:
            if kind.lower().strip() in {"checkbox", "radio", "radio button"}:
                for raw in (en, ar):
                    label = _strip_placeholder_brackets(raw)
                    if label:
                        out.add(label)
        return out

    def allows_free_text(self) -> bool:
        """True when the table has a free-text row (an 'Other reason' escape)."""
        return any(
            k.lower().strip() in {"text", "textarea", "free text"}
            for _, _, k in self.rows
        )


def _strip_placeholder_brackets(text: str) -> str:
    """``<I no longer need the product>`` -> ``I no longer need the product``."""
    return (text or "").strip().strip("<>").strip()


def parse_data_field_tables(description: str) -> list[DataFieldTable]:
    """Every DF table in the description, in order of appearance.

    A table is attributed to the most recent ``**DFnn**`` marker above it. The
    SAME declared id may appear twice -- that is a ticket defect, reported by
    :func:`source_ambiguity_issues`, not silently merged here.
    """
    tables: list[DataFieldTable] = []
    try:
        current_id: str | None = None
        rows: list[tuple[str, str, str]] = []

        def flush() -> None:
            if current_id and rows:
                tables.append(DataFieldTable(table_id=current_id, rows=tuple(rows)))

        for line in _join_wrapped_rows(description):
            if len(tables) >= _MAX_TABLES:
                logger.info(
                    "parse_data_field_tables hit the %d-table cap - truncating",
                    _MAX_TABLES,
                )
                break
            section = _section_id_match(line)
            if section:
                flush()
                rows = []
                raw_id = _WS_RE.sub("", section.group("id")).upper()
                current_id = raw_id if raw_id.startswith("DF") else None
                continue
            cells = _split_row(line)
            if not cells:
                continue
            if current_id is None:
                continue
            if len(rows) >= _MAX_ROWS_PER_TABLE:
                continue
            meaningful = [c for c in cells if c]
            if not meaningful:
                continue
            head = meaningful[0].lower()
            if head.startswith("content") or head in {"type", "bottom sheet"}:
                continue  # header row / spanning title
            ar = cells[0] if len(cells) > 0 else ""
            en = cells[1] if len(cells) > 1 else ""
            kind = cells[2] if len(cells) > 2 else ""
            rows.append((ar, en, kind))
        flush()
    except Exception:
        logger.exception("parse_data_field_tables failed - returning what was parsed")
    return tables


def parse_requirement_units(
    description: str, provenance: str = "target"
) -> list[RequirementUnit]:
    """Atomic requirement units parsed from a use-case-table description.

    Produces ``PRE-n`` / ``POST-n`` / ``BF-n`` / ``AF-n`` / ``BR-n`` from the
    use-case table and ``DFnn-n`` from each data-field table row. Returns [] for
    an empty or unparseable description -- callers treat an empty result as "no
    anchorable requirements", exactly as they do for an absent AC field.

    Never raises.
    """
    units: list[RequirementUnit] = []
    try:
        if not description or not description.strip():
            return []
        prov = "background" if provenance == "background" else "target"
        counters: dict[str, int] = {}

        def add(kind: str, text: str, explicit_id: str | None = None) -> None:
            body = _clean_cell(text)
            if len(body) < _MIN_UNIT_CHARS:
                return
            if len(units) >= _MAX_UNITS:
                if len(units) == _MAX_UNITS:
                    logger.info(
                        "parse_requirement_units hit the %d-unit cap - "
                        "later requirements are not emitted",
                        _MAX_UNITS,
                    )
                return
            if explicit_id:
                unit_id = explicit_id
            else:
                counters[kind] = counters.get(kind, 0) + 1
                unit_id = f"{_KIND_PREFIX.get(kind, 'REQ')}-{counters[kind]}"
            units.append(
                RequirementUnit(
                    unit_id=unit_id,
                    text=body[:_MAX_UNIT_CHARS],
                    kind=kind,
                    provenance=prov,
                )
            )

        for label, value in _uc_rows(description):
            kind = _label_kind(label)
            if not kind or not value:
                continue
            if kind in {"basic_flow", "alternative_flow"}:
                for step in _split_flow(value):
                    add(kind, step)
            elif kind == "business_rule":
                for _declared, body in _split_rules(value):
                    add(kind, body)
            else:
                add(kind, value)

        # A ticket may reuse a table id for two different screens (SHYJ-5645
        # labels both the confirmation dialog and the success screen DF02). That
        # is reported by source_ambiguity_issues; here the SECOND occurrence is
        # suffixed so every unit id stays unique and therefore citable.
        seen_tables: dict[str, int] = {}
        for table in parse_data_field_tables(description):
            seen_tables[table.table_id] = seen_tables.get(table.table_id, 0) + 1
            occurrence = seen_tables[table.table_id]
            table_key = table.table_id
            if occurrence > 1:
                # Numeric, not chr(ord('a') + n): past the 26th occurrence that
                # produced ids like "DF02|-1", and a pipe inside a unit id
                # corrupts any pipe-delimited rendering of the traceability matrix.
                table_key = f"{table.table_id}#{occurrence}"
            for index, (ar, en, kind_label) in enumerate(table.rows, 1):
                text = " / ".join(p for p in (en, ar) if p)
                if kind_label:
                    text = f"{text} [{kind_label}]"
                add("data_field", text, explicit_id=f"{table_key}-{index}")
    except Exception:
        logger.exception("parse_requirement_units failed - returning what was parsed")
    return units


def assignable_unit_ids(units: list[RequirementUnit]) -> set[str]:
    """Unit ids a test case is allowed to cite (target provenance only)."""
    try:
        return {u.unit_id for u in units if u.is_assignable}
    except Exception:
        logger.exception("assignable_unit_ids failed - returning empty set")
        return set()


def source_ambiguity_issues(description: str) -> list[str]:
    """Defects in the SOURCE ticket, found deterministically -- no model call.

    Each returned string is a reviewer-facing issue suitable for the step-zero
    ambiguity result. These are ticket defects, not suite defects, and they are
    reported rather than resolved: guessing which of two identically-numbered
    rules a case traces to is the behaviour this whole batch removes.

    Detects:
      * the same business-rule id declared twice (SHYJ-5645 numbers two
        different rules ``BR02``);
      * the same data-field table id used twice (SHYJ-5645 labels both the
        confirmation dialog and the success screen ``DF02``);
      * two rows of one table sharing an English label while their Arabic
        differs (SHYJ-5645's DF01 labels BOTH buttons "Keep the order", so the
        primary button's English string is wrong).

    Never raises.
    """
    issues: list[str] = []
    try:
        if not description or not description.strip():
            return []

        declared: list[str] = []
        for label, value in _uc_rows(description):
            if _label_kind(label) == "business_rule":
                declared.extend(d for d, _ in _split_rules(value) if d)
        for rule_id in sorted({d for d in declared if declared.count(d) > 1}):
            issues.append(
                f"Business rule id {rule_id} is declared {declared.count(rule_id)} times "
                f"for different rules - renumber them so each rule is citable."
            )

        tables = parse_data_field_tables(description)
        table_ids = [t.table_id for t in tables]
        for table_id in sorted({t for t in table_ids if table_ids.count(t) > 1}):
            issues.append(
                f"Data-field table id {table_id} is used {table_ids.count(table_id)} times "
                f"for different screens - give each table its own id."
            )

        for table in tables:
            by_en: dict[str, list[str]] = {}
            for ar, en, _kind in table.rows:
                key = _strip_placeholder_brackets(en).lower()
                if key:
                    by_en.setdefault(key, []).append(_strip_placeholder_brackets(ar))
            for en_label, ar_labels in sorted(by_en.items()):
                if len(ar_labels) > 1 and len({a for a in ar_labels if a}) > 1:
                    issues.append(
                        f"{table.table_id}: the English label "
                        f'"{en_label}" is used for {len(ar_labels)} different rows '
                        f"({', '.join(a for a in ar_labels if a)}) - one of the "
                        f"English strings is wrong."
                    )
    except Exception:
        logger.exception("source_ambiguity_issues failed - returning what was found")
    return issues


def enumerations(description: str) -> dict[str, set[str]]:
    """{table_id: {selectable English labels}} for every DF table that has any.

    This is the authoritative list a test case may select from, which is how
    :func:`find_unknown_enum_values` catches a case built around a reason label
    the ticket never defines.
    """
    out: dict[str, set[str]] = {}
    try:
        for table in parse_data_field_tables(description):
            values = table.enum_values()
            if values:
                out.setdefault(table.table_id, set()).update(values)
    except Exception:
        logger.exception("enumerations failed - returning what was parsed")
    return out


def free_text_tables(description: str) -> set[str]:
    """Ids of DF tables that also offer a free-text row (an 'Other' escape)."""
    try:
        return {
            t.table_id
            for t in parse_data_field_tables(description)
            if t.allows_free_text()
        }
    except Exception:
        logger.exception("free_text_tables failed - returning empty set")
        return set()


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
# Evidence in the case that the tester is going through the free-text escape
# rather than picking a predefined option -- such a value is NOT an enum member
# and must not be flagged.
_FREE_TEXT_EVIDENCE_RE = re.compile(r"other\s+reason|free[-\s]?text|سبب\s*اخر", re.I)
# A value that names a CONTROL TYPE or points at "whatever the build lists" is
# not a claim that a specific label exists, so it must not be flagged.
_GENERIC_POINTER_RE = re.compile(
    r"\b(?:first|second|third|last|any|each|all|every|some|predefined|preset|"
    r"listed|visible|available|default|valid|selected|chosen|applicable)\b"
    r"|^(?:checkbox(?:es)?|radio|button|text|option(?:s)?|reason(?:s)?)$",
    re.I,
)


def _case_text(tc: TestCase) -> str:
    """Title + preconditions + every step's action/data/expected, as one string."""
    chunks: list[str] = [
        getattr(tc, "title", "") or "",
        getattr(tc, "preconditions", "") or "",
    ]
    for step in getattr(tc, "steps", None) or []:
        chunks.append(getattr(step, "action", "") or "")
        chunks.append(getattr(step, "test_data", "") or "")
        chunks.append(getattr(step, "expected_result", "") or "")
    for item in getattr(tc, "test_data", None) or []:
        chunks.append(getattr(item, "notes", "") or "")
        # example_value too: a case whose only mention of the free-text escape
        # lives in the value itself was still being flagged.
        chunks.append(getattr(item, "example_value", "") or "")
    return " ".join(chunks)


def _normalize_label(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip().strip("<>").strip().lower())


def find_unknown_enum_values(
    cases: list[TestCase],
    enum_values: dict[str, set[str]],
    allow_free_text: bool = True,
) -> list[tuple[str, str, str]]:
    """(tc_id, field, value) for cases selecting an option the ticket never defines.

    A case is flagged only when ALL of these hold, which keeps the check free of
    the false positives that would make it useless:

      1. it carries a ``test_data`` item whose field name is enum-ish
         (``cancel_reason``, ``reason``, ...);
      2. that item's ``example_value`` is not one of the ticket's defined
         labels; and
      3. the case shows NO evidence of taking the free-text escape -- it never
         mentions "Other reason" anywhere in its title, preconditions, steps or
         notes. A case that legitimately types free text into Other reason
         always names it, so it is spared.

    On the SHYJ-5645 suite this is exactly the four unexecutable cases -- values
    "Delayed delivery", "Changed my mind" and "Wrong item" against a DF01 that
    defines three reasons -- while the case that genuinely uses Other reason
    free text is not flagged.

    Never raises.
    """
    out: list[tuple[str, str, str]] = []
    try:
        if not cases or not enum_values:
            return []
        allowed = {_normalize_label(v) for vals in enum_values.values() for v in vals}
        if not allowed:
            return []
        for tc in cases:
            items = getattr(tc, "test_data", None) or []
            if not items:
                continue
            if allow_free_text and _FREE_TEXT_EVIDENCE_RE.search(_case_text(tc)):
                continue
            for item in items:
                field = (getattr(item, "field", "") or "").lower()
                if not any(hint in field for hint in _ENUM_FIELD_HINTS):
                    continue
                if any(frag in field for frag in _NON_LABEL_FIELD_FRAGMENTS):
                    continue
                value = getattr(item, "example_value", "") or ""
                normalized = _normalize_label(value)
                if not normalized or normalized in allowed:
                    continue
                # A bare number/short token is a count or a selector, never an
                # option label the ticket was supposed to define.
                if len(normalized) < 4 or not re.search(r"[a-z؀-ۿ]", normalized):
                    continue
                if normalized[0].isdigit():
                    continue  # "2 checkboxes" is a count, not a label
                # A generic instruction ("first listed reason",
                # "first_checkbox_reason") points at whatever the build shows
                # rather than claiming a specific label. Underscores are folded
                # to spaces first: `\bfirst\b` does not match "first_checkbox"
                # because `_` is itself a word character.
                spaced = normalized.replace("_", " ").replace("-", " ")
                if _GENERIC_POINTER_RE.search(spaced):
                    continue
                out.append((getattr(tc, "tc_id", "") or "", field, value))
    except Exception:
        logger.exception("find_unknown_enum_values failed - returning what was found")
    return out


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


def find_unaddressed_requirements(
    units: list[RequirementUnit], cases: list[TestCase]
) -> list[RequirementUnit]:
    """Target units of a reportable kind that NO case shares a content word with.

    Deliberately conservative -- a unit is reported only when the overlap with
    every case is EMPTY -- because this is advisory text a human reads, and a
    gap report that cries wolf is worse than no gap report. Never raises.
    """
    try:
        eligible = [
            u
            for u in units or []
            if u.is_assignable and u.kind in _COVERAGE_KINDS and u.text
        ]
        if not eligible or not cases:
            return []
        case_terms: set[str] = set()
        for tc in cases:
            case_terms |= {
                w for w in _WORD_RE.findall(_case_text(tc).lower()) if len(w) > 3
            }
        if not case_terms:
            return []
        uncovered = []
        for unit in eligible:
            terms = {w for w in _WORD_RE.findall(unit.text.lower()) if len(w) > 3}
            if terms and not (terms & case_terms):
                uncovered.append(unit)
        if len(uncovered) > len(eligible) * _MAX_UNCOVERED_RATIO:
            logger.info(
                "Uncovered-requirement heuristic flagged %d/%d units - suppressing "
                "as a vocabulary mismatch rather than a real gap",
                len(uncovered),
                len(eligible),
            )
            return []
        return uncovered
    except Exception:
        logger.exception("find_unaddressed_requirements failed - reporting none")
        return []


def coverage_warning_section(uncovered: list[RequirementUnit]) -> str:
    """Advisory markdown listing requirements no case appears to exercise."""
    try:
        if not uncovered:
            return ""
        lines = [
            "\n\n## Requirements With No Matching Case (advisory)",
            "",
            "Parsed from the ticket, but no generated case mentions them. Confirm "
            "they are covered or add cases:",
        ]
        for unit in uncovered[:10]:
            lines.append(f"- **{unit.unit_id}**: {unit.text[:160]}")
        if len(uncovered) > 10:
            lines.append(f"- ... and {len(uncovered) - 10} more")
        return "\n".join(lines)
    except Exception:
        logger.exception("coverage_warning_section failed - returning empty string")
        return ""


def enum_warning_section(
    violations: list[tuple[str, str, str]], enum_values: dict[str, set[str]]
) -> str:
    """Advisory markdown listing enum-value violations. '' when there are none."""
    try:
        if not violations:
            return ""
        defined = sorted({v for vals in enum_values.values() for v in vals})
        lines = [
            "\n\n## Undefined Option Values (advisory)",
            "",
            "These cases select an option the ticket does not define, so a tester "
            "cannot execute them as written:",
        ]
        for tc_id, field, value in violations[:20]:
            lines.append(f'- **{tc_id}** - `{field}` = "{value}"')
        if len(violations) > 20:
            lines.append(f"- ... and {len(violations) - 20} more")
        if defined:
            lines.append("")
            lines.append("Defined options: " + ", ".join(f'"{d}"' for d in defined))
        return "\n".join(lines)
    except Exception:
        logger.exception("enum_warning_section failed - returning empty string")
        return ""
