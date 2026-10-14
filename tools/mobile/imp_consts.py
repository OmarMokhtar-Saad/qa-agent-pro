"""Caps, regexes, column aliases and lookups of the case importers."""

from __future__ import annotations

import re

from tools.models import Priority, TestType
from tools.safe_parse import XML_DECL_RE

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 500
MAX_CELL_CHARS = 8000
MAX_STEPS = 40

# xlsx hardening
MAX_ZIP_MEMBERS = 200
MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024

_DECL_RE = XML_DECL_RE
_STEP_SPLIT_RE = re.compile(r"(?m)^\s*(\d{1,3})\s*[.)\-:]\s+")
_BIDI_RE = re.compile("[‎‏‪-‮⁦-⁩]")
_CELL_REF_RE = re.compile(r"^([A-Z]{1,3})(\d{1,7})$")

SHEET_NAME = "Test Cases"

# Every alias is compared case-insensitively with runs of non-alphanumerics
# collapsed, so "TC ID", "tc_id" and "TC-Id" are one column.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "tc_id": ("tcid", "id", "caseid", "testcaseid", "tc"),
    "title": ("title", "name", "summary", "testcase", "scenario"),
    "module": ("module", "feature", "area", "component"),
    "priority": ("priority", "severity"),
    "type": ("testtype", "type", "category"),
    "preconditions": ("preconditions", "precondition", "setup", "pre"),
    "steps": ("stepsactions", "steps", "step", "actions", "action", "teststeps"),
    "expected": (
        "expectedresults",
        "expectedresult",
        "expected",
        "result",
        "results",
        "expectedoutcome",
    ),
}

#: For a step the tester simply did not write an outcome for. Distinct from
#: TRAILING_EXPECTED_NOTE, which POINTS at the final step: on the partial-list
#: path the final step carries a note too, so re-using it sent the reader to a
#: step that answers nothing.
UNCOVERED_STEP_NOTE = "No expected result was supplied for this step."

TRAILING_EXPECTED_NOTE = (
    "See the expected result recorded on the final step of this case."
)

PRIORITY_LOOKUP = {p.value.lower(): p for p in Priority}
TYPE_LOOKUP = {t.value.lower(): t for t in TestType}
