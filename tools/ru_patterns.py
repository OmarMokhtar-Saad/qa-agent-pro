"""Requirement-unit regexes: the one home of every compiled pattern."""

from __future__ import annotations

import re

# A markdown pipe-table row: | cell | cell | ...
TABLE_ROW_RE = re.compile(r"^\s*\|(?P<body>.+)\|\s*$")
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
SECTION_ID_RE = re.compile(
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
WS_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"\S+")

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

_WS_RE = WS_RE  # legacy name (WX removes it)

_TABLE_ROW_RE = TABLE_ROW_RE  # legacy name (WX removes it)

_SECTION_ID_RE = SECTION_ID_RE  # legacy name (WX removes it)
