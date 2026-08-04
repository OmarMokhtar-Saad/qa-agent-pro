"""Zephyr for Jira import export -- a Zephyr-shaped XLSX plus its matching
``zfj_import_config.json`` column map, written as an inseparable PAIR.

Why this module exists
----------------------
``tools/xlsx_generator.py`` emits a generic 12-column QA worksheet (TC ID,
Module, Title, Priority, Test Type, Preconditions, Steps / Actions, Test Data,
Expected Results, Status, Notes, Coverage Category). That is an excellent tester
deliverable and
a useless Zephyr import file, so testers hand-massage the sheet to get cases
into Jira, and ``tools/testrail_exporter.py`` only mentions Zephyr in its
docstring while emitting TestRail's CSV shape. This module covers that last
mile: the 15-column Zephyr import shape AND the field-mapping JSON, both
dropped into ONE per-export folder so the two can never drift apart.

THE COLUMN LAYOUT IS NOT VENDOR-VERIFIED
----------------------------------------
``FORMAT_VERIFIED`` is False and every generated ``zfj_import_config.json``
says so. The multi-row-by-External-ID step layout below is modelled on how
Zephyr for Jira / Squad's file importer is understood to behave, but it has
NOT been checked against the vendor's own sample template or a live import.
Callers therefore default to DRY RUN (``dry_run=True``): the workbook holds a
single case, so the first thing anyone imports is ONE test into a sandbox
project rather than a whole suite into production Jira. See
operations/runbook.md -> "Zephyr export pilot gate" before turning that off.

Researched pitfalls this shape deliberately defends against
-----------------------------------------------------------
* MULTI-STEP TESTS. Zephyr's importer understands two step formats: one row
  per step keyed by a repeated External ID, or a structured Teststep custom
  field. A single cell holding every numbered step imports as ONE step and the
  rest of the case is silently lost. We therefore emit MULTI-ROW: the first row
  of a case carries every case-level column; each following row repeats only
  the External ID plus that step's action / expected result.
* EXTERNAL ID IDENTITY. Zephyr de-duplicates on External ID, so a POSITIONAL
  id (1, 2, 3 ...) is actively dangerous: suite B's "1" would match and
  OVERWRITE suite A's first test, and inserting a case would shift every later
  id so a re-import updates the wrong tests. The id is therefore
  content-derived and suite-scoped -- "<suite fragment>-<case content hash>",
  built from ``TestSuite.suite_id`` plus ``TestCase.stable_id``, the key
  tools/models.py already defines as "a stable identity across regenerations
  and exports" and tools/suite_store.py already persists.
* ISSUE LINK TYPE / ISSUE (columns I / J). Linking a test to its story during
  import is not a documented, mappable Zephyr field. The columns are emitted
  (the approved shape, and the human record of the intended link) but the JSON
  marks them ``"mappable": false`` and repeats every external-id -> story-key
  pair under ``post_import_linking`` so the link can be made afterwards over
  the Jira REST API or in the GUI. Nothing here claims the link happened.
* PROJECT / ISSUE TYPE (columns B / C). Both are chosen upfront in the importer
  UI and apply to the whole file; per-row variation is impossible. One constant
  value is written on each case's first row and the JSON says so.
* RE-IMPORT DE-DUPLICATION. Zephyr only matches on External ID once its
  External Issue ID custom field exists, which happens on the first MAPPED
  import. Our ids are stable, but that precondition is not ours to keep, so the
  JSON states it and its failure mode instead of promising de-duplication.
* ZEPHYR SCALE. Scale imports over REST, not by column mapping. This file
  targets Zephyr for Jira / Zephyr Squad; the JSON declares the product so a
  Scale user finds out before importing, not after.
* WHERE THE PAIR LANDS. Workbook + JSON go into ONE per-export folder under the
  caller's directory (``QA_EXPORT_DIR`` / the folder the auto-exported Excel
  file landed in). That folder is the tester's PERMANENT deliverable: nothing
  sweeps it, because the JSON must survive to the next re-import. Only when no
  directory is supplied does it fall back to the shared 0700 secure temp dir.
* FORMULA INJECTION. Every text cell goes through
  ``tools.cell_sanitizer.sanitize_cell``, exactly like the other exporters, and
  the JSON warns against hand-editing the workbook before a re-import.

Priority (column K) comes from ``tools/risk_scorer.py``'s ``risk_label``,
falling back to the case's own Priority. The reference implementation's
``infer_priority()`` keyword heuristic is deliberately NOT ported: its own
documentation admits it treats "session" as a High signal and so marks nearly
every case High on any story about sessions / calls / appointments.

Opt-in: nothing here runs unless ``QA_ZEPHYR_EXPORT_ENABLED`` is on, and
``QA_ZEPHYR_DRY_RUN`` (default ON) decides whether the workbook is a one-case
pilot or the full suite. This module reads NO settings itself -- ``dry_run`` is
a parameter whose default is the SAFE value -- so it stays a dependency-free
file writer that tools/mcp_handlers.py gates.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import xlsxwriter

from tools.bilingual import bidi_isolate, is_rtl_cell
from tools.cell_sanitizer import sanitize_cell
from tools.models import TestSuite, format_test_data_lines
from tools.secure_temp import secure_temp_dir

logger = logging.getLogger(__name__)

# Bumped whenever the column list / row layout / id scheme changes. It is hashed
# into CONFIG_HASH, which is written into the config JSON, so a stale mapping
# kept from an older release can be spotted instead of silently mis-mapping.
SCHEMA_VERSION = "1.1"

# The importer family this file targets. Zephyr Squad is the renamed Zephyr for
# Jira and uses the same file-based mapping importer. Zephyr SCALE does not --
# it imports over REST -- which is why the value is declared in the JSON.
ZEPHYR_PRODUCT = "zephyr-for-jira"

# HONEST DEFAULT: the layout has not been checked against the vendor's sample
# template or a live import. Flip this (and replace tests/fixtures/
# zephyr_import_layout.golden.tsv with the vendor template) only in the commit
# that records a passing pilot in operations/runbook.md.
FORMAT_VERIFIED = False
PILOT_GATE_DOC = 'operations/runbook.md -> "Zephyr export pilot gate"'

CONFIG_FILENAME = "zfj_import_config.json"
WORKBOOK_FILENAME = "zephyr_import.xlsx"
PILOT_WORKBOOK_FILENAME = "zephyr_import_PILOT.xlsx"
SHEET_NAME = "Zephyr Import"

# How many cases a dry-run (pilot) workbook emits. One is enough to prove the
# column mapping and the multi-row step layout, and small enough that a wrong
# guess costs a single throwaway test in a sandbox project.
PILOT_CASE_LIMIT = 1

# Constant, whole-file values (see the PROJECT / ISSUE TYPE note above).
ISSUE_TYPE = "Test"
ISSUE_LINK_TYPE = "tests"

# External ID construction (see the EXTERNAL ID IDENTITY note above).
EXTERNAL_ID_FORMAT = "<suite-fragment>-<case-content-hash>"
_SUITE_FRAG_CHARS = 8
_CONTENT_HASH_CHARS = 10
_STABLE_ID_PREFIX = "SID-"

_COL_EXTERNAL_ID = 0
_COL_PROJECT = 1
_COL_ISSUE_TYPE = 2
_COL_NAME = 3
_COL_ASSIGNEE = 4
_COL_DESCRIPTION = 5
_COL_TEST_STEPS = 6
_COL_EXPECTED = 7
_COL_ISSUE_LINK_TYPE = 8
_COL_ISSUE = 9
_COL_PRIORITY = 10
_COL_LABELS = 11
_COL_EPIC_LINK = 12
_COL_FIX_VERSIONS = 13
_COL_SPRINT = 14
_TOTAL_COLS = 15

# Column D's header is literally "Name\Summary" in Zephyr's importer.
_HEADERS = [
    "External ID",
    "Project",
    "Issue Type",
    "Name\\Summary",
    "Assignee",
    "Description",
    "Test Steps",
    "Expected Results",
    "Issue Link Type",
    "Issue",
    "Priority",
    "Labels",
    "Epic Link",
    "Fix versions",
    "Sprint",
]

_COL_WIDTHS = [22, 12, 12, 45, 14, 40, 45, 45, 16, 14, 12, 22, 14, 14, 14]

# Header row is bold 16pt per the agreed sheet style; data rows wrap.
_HEADER_ROW_HEIGHT = 26

_COLUMN_NOTES = [
    "Content-derived id, repeated on every row of a multi-step case. MAP THIS "
    "COLUMN: unmapped, each step row imports as a separate one-step test.",
    "Informational only -- the importer applies ONE project, chosen upfront in "
    "its UI, to the whole file.",
    "Informational only -- the issue type is chosen upfront too.",
    "Maps to the Jira summary.",
    "Intentionally blank -- assign after import.",
    "Preconditions plus the data-provisioning plan.",
    "One step per row (multi-row format).",
    "The expected result of the step on the same row.",
    "NOT a mappable Zephyr field -- see post_import_linking.",
    "NOT a mappable Zephyr field -- see post_import_linking.",
    "Derived from tools/risk_scorer.py's risk label.",
    "';'-separated Jira labels.",
    "Blank unless the caller supplied one.",
    "Blank unless the caller supplied one.",
    "Blank unless the caller supplied one.",
]

# risk_scorer's tier label -> a default Jira priority name.
_RISK_TO_JIRA_PRIORITY = {
    "CRITICAL": "Highest",
    "HIGH": "High",
    "MEDIUM": "Medium",
    "LOW": "Low",
}
# Fallback when the suite was never risk-scored (risk_label == "").
_CASE_TO_JIRA_PRIORITY = {
    "Critical": "Highest",
    "High": "High",
    "Medium": "Medium",
    "Low": "Low",
}

# A Jira issue key: PROJECT-123. The prefix denylist stops obvious false
# positives ("UTF-8", "SHA-256", "ISO-27001") from being mistaken for a story
# key and routing the whole import at a project that does not exist.
_ISSUE_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9_]{1,20})-(\d{1,10})\b")
_BROWSE_KEY_RE = re.compile(r"/(?:browse|issues)/([A-Z][A-Z0-9_]{1,20}-\d{1,10})\b")
# Anything outside this set is replaced before a suite id becomes a folder name
# or an External ID prefix, so a hostile / odd suite_id can never introduce a
# path separator.
_UNSAFE_FRAG_RE = re.compile(r"[^A-Za-z0-9_.-]+")

_KEY_PREFIX_DENYLIST = frozenset(
    {
        "AES",
        "GDPR",
        "HTTP",
        "HTTPS",
        "IPV",
        "ISO",
        "PCI",
        "RFC",
        "RSA",
        "SHA",
        "SOC",
        "TLS",
        "UTF",
    }
)


def _config_hash() -> str:
    """Short, stable hash of the emitted schema (headers + layout + id scheme).

    Written into the config JSON so a ``zfj_import_config.json`` kept from an
    older release can be spotted next to a newer workbook instead of silently
    mis-mapping columns. Pure; never raises.
    """
    payload = json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "headers": _HEADERS,
            "multi_step_format": "multi-row-by-external-id",
            "external_id_format": EXTERNAL_ID_FORMAT,
            "product": ZEPHYR_PRODUCT,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


CONFIG_HASH = _config_hash()


def derive_story_key(text: str) -> str:
    """Best-effort Jira issue key from a ticket URL or free text ("" if none).

    A ``/browse/KEY`` (or ``/issues/KEY``) path segment wins over a bare in-text
    match, because that is the ticket the tester actually pointed at. Pure and
    never raises -- an unknown key exports blank Project / Issue cells plus a
    warning in the config JSON, never a crash and never a guess.
    """
    try:
        raw = text or ""
        match = _BROWSE_KEY_RE.search(raw)
        if match:
            return match.group(1).upper()
        for candidate in _ISSUE_KEY_RE.finditer(raw):
            prefix = candidate.group(1).upper()
            if prefix in _KEY_PREFIX_DENYLIST:
                continue
            return f"{prefix}-{candidate.group(2)}"
    except Exception:  # pragma: no cover - defensive
        logger.debug("derive_story_key failed -- returning ''", exc_info=True)
    return ""


def project_from_story_key(story_key: str) -> str:
    """The project prefix of a Jira key ("SHYJ-7154" -> "SHYJ"). Never raises."""
    try:
        key = (story_key or "").strip().upper()
        return key.rsplit("-", 1)[0] if "-" in key else ""
    except Exception:  # pragma: no cover - defensive
        return ""


def jira_priority_for(tc) -> str:
    """Column K. risk_scorer's risk_label first, the case Priority as fallback.

    Never raises and never returns "" -- an unmapped value degrades to "Medium"
    so the importer is never handed a blank priority. The reference
    implementation's keyword heuristic is deliberately not used here.
    """
    try:
        label = (getattr(tc, "risk_label", "") or "").strip().upper()
        if label in _RISK_TO_JIRA_PRIORITY:
            return _RISK_TO_JIRA_PRIORITY[label]
        priority = getattr(getattr(tc, "priority", None), "value", "") or ""
        return _CASE_TO_JIRA_PRIORITY.get(priority, "Medium")
    except Exception:  # pragma: no cover - defensive
        return "Medium"


# --------------------------------------------------------------------------- #
# External ID -- the identity contract
# --------------------------------------------------------------------------- #


def suite_fragment(suite) -> str:
    """A filesystem- and Jira-safe fragment of the suite id (<= 8 chars).

    Used for BOTH the per-export folder name and the External ID prefix.
    ``TestSuite.suite_id`` is a free-form string (tools/models.py sets no
    pattern), so an id like ``"../../etc"`` would otherwise be spliced into the
    export path and walk the folder out of its base directory. Slugify, strip
    leading/trailing dots and dashes, fall back to "suite" when nothing
    survives. Pure; never raises.
    """
    raw = str(getattr(suite, "suite_id", "") or "")
    frag = _UNSAFE_FRAG_RE.sub("-", raw).strip("-.")[:_SUITE_FRAG_CHARS].strip("-.")
    return frag or "suite"


def _fallback_content_hash(tc) -> str:
    """Content hash for a case that carries no ``stable_id``.

    A validated ``TestCase`` always has one (a model validator assigns it), so
    this only fires for duck-typed callers. Its single requirement is
    DETERMINISM for identical content -- it deliberately does not try to
    reproduce ``models._compute_stable_id`` byte for byte, because such a case
    never round-trips through tools/suite_store.py anyway.
    """
    parts = [str(getattr(tc, "title", "") or "").strip()]
    for step in getattr(tc, "steps", []) or []:
        parts.append(str(getattr(step, "action", "") or "").strip())
        parts.append(str(getattr(step, "expected_result", "") or "").strip())
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _content_hash(tc) -> str:
    """``_CONTENT_HASH_CHARS`` hex chars identifying a case's CONTENT.

    Prefers ``TestCase.stable_id`` (sha256 of the title and ordered steps) with
    its ``SID-`` prefix stripped, so the id qa-agents already persists and
    de-dupes on is the id Zephyr sees. Never raises.
    """
    try:
        sid = str(getattr(tc, "stable_id", "") or "").strip()
        if sid.startswith(_STABLE_ID_PREFIX):
            sid = sid[len(_STABLE_ID_PREFIX) :]
        digest = re.sub(r"[^0-9a-f]", "", sid.lower())
        if len(digest) < _CONTENT_HASH_CHARS:
            digest = _fallback_content_hash(tc)
        return digest[:_CONTENT_HASH_CHARS]
    except Exception:  # pragma: no cover - defensive
        logger.debug("zephyr: content hash unavailable", exc_info=True)
        return "0" * _CONTENT_HASH_CHARS


def external_id_for(suite, tc) -> str:
    """Column A: a STABLE, suite-scoped External ID -- never a row index.

    ``"<suite fragment>-<case content hash>"``. Zephyr de-duplicates on this
    value, so:

    * re-exporting the SAME stored suite yields byte-identical ids, letting a
      mapped re-import update those tests instead of duplicating them;
    * a DIFFERENT suite gets a different fragment, so its rows can never claim
      -- and overwrite -- another suite's tests;
    * inserting or removing a case shifts nothing, because nothing is
      positional;
    * editing a case's title or steps changes its id, so the edited case
      imports as a new test (documented in the config JSON).

    Pure; never raises.
    """
    return f"{suite_fragment(suite)}-{_content_hash(tc)}"


def assign_external_ids(suite) -> list[tuple[str, object]]:
    """``(external_id, case)`` in emission order. Deterministic; never raises.

    Two cases with identical content share a content hash by construction --
    they are the same test -- so the second and later occurrences get a
    deterministic "-2", "-3" ... suffix and ``validate_for_zephyr`` reports
    them as an ACTIONABLE warning (a suite should not contain duplicates).
    """
    pairs: list[tuple[str, object]] = []
    try:
        seen: dict[str, int] = {}
        for tc in list(getattr(suite, "test_cases", []) or []):
            base = external_id_for(suite, tc)
            seen[base] = seen.get(base, 0) + 1
            pairs.append((base if seen[base] == 1 else f"{base}-{seen[base]}", tc))
    except Exception:  # pragma: no cover - defensive
        logger.exception("zephyr: external id assignment failed")
    return pairs


def pilot_indices(cases) -> list[int]:
    """Which case indices a DRY-RUN (pilot) workbook emits.

    The first MULTI-STEP case when the suite has one, else the first case: the
    multi-row step layout is precisely the part that is not vendor-verified, so
    a pilot built from a single-step case would prove nothing. Never raises.
    """
    try:
        items = list(cases or [])
        multi = [
            i
            for i, tc in enumerate(items)
            if len(list(getattr(tc, "steps", []) or [])) > 1
        ]
        chosen = multi or list(range(len(items)))
        return chosen[:PILOT_CASE_LIMIT]
    except Exception:  # pragma: no cover - defensive
        return []


# --------------------------------------------------------------------------- #
# Row / cell rendering
# --------------------------------------------------------------------------- #


def _description_for(tc) -> str:
    """Column F: the case's preconditions plus its data-provisioning plan.

    Zephyr's Description is the only free-text home for "what must be true
    before this test runs", which is exactly our preconditions field.
    """
    parts: list[str] = []
    pre = (getattr(tc, "preconditions", "") or "").strip()
    if pre:
        parts.append(f"Preconditions: {pre}")
    data_lines = format_test_data_lines(getattr(tc, "test_data", []) or [])
    if data_lines:
        parts.append("Test data:\n" + "\n".join(data_lines))
    step_data = [
        f"Step {s.step_number}: {s.test_data}"
        for s in (getattr(tc, "steps", []) or [])
        if getattr(s, "test_data", None)
    ]
    if step_data:
        parts.append("Step data:\n" + "\n".join(step_data))
    return "\n\n".join(parts)


def _labels_for(tc, extra_labels: tuple[str, ...] = ()) -> str:
    """Column L: any caller labels plus the case's test type, as a slug list.

    Jira labels cannot contain whitespace, so each label is slugified. The
    separator is ';' -- the Zephyr importer's documented multi-value split.
    """
    values: list[str] = []
    for raw in (*extra_labels, getattr(getattr(tc, "type", None), "value", "") or ""):
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(raw).strip()).strip("-").lower()
        if slug and slug not in values:
            values.append(slug)
    return ";".join(values)


def case_rows(
    tc,
    external_id: str,
    *,
    story_key: str = "",
    labels: str = "",
    epic_link: str = "",
    fix_versions: str = "",
    sprint: str = "",
) -> list[list[str]]:
    """The MULTI-ROW Zephyr representation of one test case.

    Row 1 carries every case-level column; each subsequent row repeats only the
    External ID plus that step's action / expected result. Collapsing the steps
    into one cell would import only the first step and silently drop the rest.

    ``external_id`` is the content-derived string from ``external_id_for`` --
    never a row index. Every text cell is sanitize_cell()'d: this content is
    LLM-generated or Jira-derived and must never become a spreadsheet formula.
    """
    project = project_from_story_key(story_key)
    steps = list(getattr(tc, "steps", []) or [])
    rows: list[list[str]] = []
    for index, step in enumerate(steps or [None]):
        row = [""] * _TOTAL_COLS
        row[_COL_EXTERNAL_ID] = sanitize_cell(str(external_id or ""))
        action = getattr(step, "action", "") if step is not None else ""
        expected = getattr(step, "expected_result", "") if step is not None else ""
        row[_COL_TEST_STEPS] = sanitize_cell(str(action or ""))
        row[_COL_EXPECTED] = sanitize_cell(str(expected or ""))
        if index == 0:
            row[_COL_PROJECT] = sanitize_cell(project)
            row[_COL_ISSUE_TYPE] = ISSUE_TYPE
            row[_COL_NAME] = sanitize_cell(str(getattr(tc, "title", "") or ""))
            row[_COL_ASSIGNEE] = ""
            row[_COL_DESCRIPTION] = sanitize_cell(_description_for(tc))
            # Emitted for the human record only -- see the module docstring and
            # the config JSON's post_import_linking block.
            row[_COL_ISSUE_LINK_TYPE] = ISSUE_LINK_TYPE if story_key else ""
            row[_COL_ISSUE] = sanitize_cell(story_key)
            row[_COL_PRIORITY] = jira_priority_for(tc)
            row[_COL_LABELS] = sanitize_cell(labels)
            row[_COL_EPIC_LINK] = sanitize_cell(epic_link)
            row[_COL_FIX_VERSIONS] = sanitize_cell(fix_versions)
            row[_COL_SPRINT] = sanitize_cell(sprint)
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# Validation gate + config JSON
# --------------------------------------------------------------------------- #


def validate_for_zephyr(
    suite,
    story_key: str = "",
    *,
    dry_run: bool = False,
    emitted_cases: int | None = None,
) -> dict:
    """Pre-write gate -> ``{"warnings": [...], "notes": [...]}``. Never raises.

    ``warnings`` holds ACTIONABLE problems only -- things a human can fix before
    importing: an empty suite, a case with no steps, no story key, a key with no
    project prefix, content-identical cases sharing one External ID.
    ``notes`` holds facts that are always true of a CORRECT export (the
    multi-row step layout, the unverified format, pilot mode) so a real problem
    is never buried in boilerplate. Both are logged AND embedded in
    zfj_import_config.json so they are visible before the import, not after Jira
    has been polluted.
    """
    warnings: list[str] = []
    notes: list[str] = []
    try:
        cases = list(getattr(suite, "test_cases", []) or [])
        if not cases:
            warnings.append("The suite has no test cases -- the workbook is empty.")
        # Reachable only through a duck-typed caller: tools/models.py pins
        # TestCase.steps to min_length=1.
        stepless = [
            str(getattr(tc, "tc_id", "?"))
            for tc in cases
            if not (getattr(tc, "steps", []) or [])
        ]
        if stepless:
            warnings.append(
                "These cases have no steps and would import as an empty test: "
                + ", ".join(stepless[:10])
            )
        # External IDs are content-derived, so a collision is a REAL signal (two
        # cases with the same title and steps) rather than the tautology a
        # positional-id scheme would produce.
        buckets: dict[str, list[str]] = {}
        for tc in cases:
            buckets.setdefault(external_id_for(suite, tc), []).append(
                str(getattr(tc, "tc_id", "?"))
            )
        dupes = [ids for ids in buckets.values() if len(ids) > 1]
        if dupes:
            warnings.append(
                "These cases are content-identical, so they share one content "
                "hash and the 2nd+ get a '-2'/'-3' External ID suffix. "
                "De-duplicate the suite instead: "
                + "; ".join(", ".join(ids) for ids in dupes[:5])
            )
        if not story_key:
            warnings.append(
                "No Jira story key was available, so Project (B), Issue (J) and "
                "Issue Link Type (I) are blank. Pick the project in the importer "
                "UI and link the tests to their story after import."
            )
        elif not project_from_story_key(story_key):
            warnings.append(
                f"Story key {story_key!r} has no project prefix -- Project (B) "
                "is blank."
            )
        multi_step = sum(1 for tc in cases if len(getattr(tc, "steps", []) or []) > 1)
        if multi_step:
            notes.append(
                f"{multi_step} case(s) are multi-step and are emitted as several "
                "rows sharing one External ID (Zephyr's multi-row step format). "
                "Map the External ID column, or the extra rows import as "
                "separate one-step tests."
            )
        if not FORMAT_VERIFIED:
            notes.append(
                "This column layout has NOT been verified against a live Zephyr "
                "importer or the vendor's sample template. Import into a sandbox "
                f"project first -- see {PILOT_GATE_DOC}."
            )
        if dry_run:
            shown = PILOT_CASE_LIMIT if emitted_cases is None else emitted_cases
            notes.append(
                f"PILOT workbook: only {shown} of {len(cases)} case(s) were "
                "written because QA_ZEPHYR_DRY_RUN is on. Verify the import, "
                "then set QA_ZEPHYR_DRY_RUN=false for the full suite."
            )
    except Exception:
        logger.exception("Zephyr validation gate failed -- continuing without it")
    return {"warnings": warnings, "notes": notes}


def workbook_name(dry_run: bool = False) -> str:
    """The workbook filename. A pilot file is NAMED like one so it can never be
    mistaken for the full deliverable. Never raises."""
    return PILOT_WORKBOOK_FILENAME if dry_run else WORKBOOK_FILENAME


def build_import_config(
    suite,
    *,
    story_key: str = "",
    validation: dict | None = None,
    row_count: int = 0,
    dry_run: bool = False,
    id_pairs: list[tuple[str, object]] | None = None,
) -> dict:
    """The ``zfj_import_config.json`` payload. Pure; never raises.

    ``id_pairs`` MUST be the same ``(external_id, case)`` list the worksheet
    rows were written from, so ``post_import_linking`` can never disagree with
    the sheet.
    """
    all_cases = list(getattr(suite, "test_cases", []) or [])
    pairs = list(id_pairs if id_pairs is not None else assign_external_ids(suite))
    checks = validation or {"warnings": [], "notes": []}
    column_map = [
        {
            "column": chr(ord("A") + i),
            "header": header,
            "zephyr_field": header,
            "mappable": header not in ("Issue Link Type", "Issue"),
            "note": note,
        }
        for i, (header, note) in enumerate(zip(_HEADERS, _COLUMN_NOTES))
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "config_hash": CONFIG_HASH,
        "generator": "qa-agents/tools/zephyr_exporter.py",
        # Timezone-aware ISO-8601: this file is KEPT and compared across
        # releases, so a naive local timestamp would be ambiguous.
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "zephyr_product": ZEPHYR_PRODUCT,
        "not_compatible_with": ["zephyr-scale"],
        "format_verified": FORMAT_VERIFIED,
        "verification": {
            "status": "verified" if FORMAT_VERIFIED else "unverified",
            "meaning": (
                "This column layout has not been checked against a live Zephyr "
                "importer or the vendor's sample template. Pilot it on a sandbox "
                "project before importing into a production project."
            ),
            "gate": PILOT_GATE_DOC,
        },
        "immutable": True,
        "dry_run": bool(dry_run),
        "pilot": {
            "active": bool(dry_run),
            "case_limit": PILOT_CASE_LIMIT,
            "emitted_cases": len(pairs),
            "suite_case_count": len(all_cases),
            "disable_with": "QA_ZEPHYR_DRY_RUN=false",
        },
        "workbook": workbook_name(dry_run),
        "sheet": SHEET_NAME,
        "header_row": 1,
        "first_data_row": 2,
        "multi_step_format": "multi-row-by-external-id",
        "external_id_scheme": {
            "format": EXTERNAL_ID_FORMAT,
            "suite_fragment": suite_fragment(suite),
            "derived_from": (
                "TestSuite.suite_id + TestCase.stable_id (sha256 of the title "
                "and ordered steps) -- tools/models.py"
            ),
            "stable_across": (
                "re-exports of THIS suite: identical case content always yields "
                "the identical External ID"
            ),
            "changes_when": (
                "a case's title or steps change, or the feature is regenerated "
                "under a new suite_id (a new suite CREATES tests rather than "
                "updating the previous run's)"
            ),
            "not_a_row_index": (
                "Positional ids would let one suite's rows overwrite another "
                "suite's tests on import."
            ),
        },
        "suite_id": getattr(suite, "suite_id", ""),
        "suite_case_count": len(all_cases),
        "case_count": len(pairs),
        "row_count": row_count,
        "column_map": column_map,
        "post_import_linking": {
            "required": bool(story_key),
            "reason": (
                "Zephyr's file importer cannot link a test to a story. Create "
                "the tests first, then link each one to the issue below with the "
                "'tests' link type via the Jira REST API or the issue screen."
            ),
            "issue_link_type": ISSUE_LINK_TYPE,
            "pairs": [
                {
                    "external_id": external_id,
                    "issue": story_key,
                    "name": getattr(tc, "title", ""),
                }
                for external_id, tc in pairs
            ],
        },
        "validation": {
            "warnings": list(checks.get("warnings") or []),
            "notes": list(checks.get("notes") or []),
        },
        "notes": [
            "KEEP THIS FILE next to the workbook -- it is the field mapping for a "
            "re-import. The External IDs are content-derived and stable, but "
            "Zephyr can only USE them to update existing tests if your FIRST "
            "import mapped column A and created its External Issue ID custom "
            "field. If it did not, a re-import creates DUPLICATES -- verify on a "
            "sandbox project before re-importing into production.",
            "Do not hand-edit the workbook before re-importing: a cell you type "
            "starting with = + - or @ is a spreadsheet formula this exporter "
            "would otherwise have neutralised.",
            "This is the Zephyr for Jira / Squad file-mapping format. Zephyr "
            "Scale imports over REST and will not accept this workbook.",
            "Columns I (Issue Link Type) and J (Issue) are a human record only: "
            "Zephyr's file importer cannot link a test to a story. Use the "
            "post_import_linking pairs afterwards.",
        ],
    }


def _restrict(path: str) -> None:
    """Best-effort 0600 on a written artifact. Never raises.

    The per-export folder is already created 0700, but the pair itself lists
    every test title for the ticket (and, in the JSON, the story key), so the
    files are locked down too rather than left at the process umask -- the
    same posture tools/secure_temp.py takes for the shared export dir.
    """
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - platform / filesystem dependent
        logger.debug("zephyr: could not restrict %s", path, exc_info=True)


def config_path_for(xlsx_path: str) -> str:
    """The zfj_import_config.json that belongs to a generated workbook."""
    return str(Path(xlsx_path).parent / CONFIG_FILENAME)


# --------------------------------------------------------------------------- #
# Where the pair lands
# --------------------------------------------------------------------------- #


def _export_base(output_dir: str | None) -> Path:
    """The directory the per-export folder is created inside.

    ``output_dir`` -- QA_EXPORT_DIR, or the folder the auto-exported Excel file
    landed in -- wins, because the workbook + ``zfj_import_config.json`` pair is
    a KEEP-THIS-FILE deliverable and must not be dropped into a sweepable temp
    folder. An UNUSABLE directory must degrade rather than fail the export
    (mirroring tools/mcp_handlers.py's qa_export_dir fallback), so it drops to
    the shared 0700 secure export dir every other exporter uses.
    """
    if output_dir:
        try:
            base = Path(output_dir).expanduser()
            base.mkdir(parents=True, exist_ok=True)
            return base
        except (OSError, RuntimeError, ValueError):
            logger.warning(
                "Zephyr export dir %r is unusable -- falling back to the secure "
                "temp export dir",
                output_dir,
                exc_info=True,
            )
    return secure_temp_dir()


def _export_dir(output_dir: str | None, suite, dry_run: bool = False) -> Path:
    """The per-export folder holding the workbook + its config JSON.

    One folder per export keeps the config file at its canonical
    ``zfj_import_config.json`` name without two exports overwriting each other,
    and makes the pair impossible to separate by accident. A pilot export is
    named ``zephyr_pilot_*`` so it is obvious at a glance.
    """
    frag = suite_fragment(suite)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    prefix = "zephyr_pilot" if dry_run else "zephyr"
    base = _export_base(output_dir)
    # Two exports of the same suite inside one second must not overwrite each
    # other's pair, so the name gets a "-2", "-3", ... suffix on collision.
    for suffix in ("", *(f"-{n}" for n in range(2, 100))):
        target = base / f"{prefix}_{frag}_{stamp}{suffix}"
        if not target.exists():
            target.mkdir(mode=0o700, parents=True)
            return target
    # 99 exports of one suite inside one second: fall back to a random name.
    target = base / f"{prefix}_{frag}_{uuid.uuid4().hex[:8]}"  # pragma: no cover
    target.mkdir(mode=0o700, parents=True, exist_ok=True)  # pragma: no cover
    return target  # pragma: no cover


def generate_zephyr_export(
    suite: TestSuite,
    output_dir: str | None = None,
    *,
    story_key: str = "",
    dry_run: bool = True,
    extra_labels: tuple[str, ...] = (),
    epic_link: str = "",
    fix_versions: str = "",
    sprint: str = "",
) -> str:
    """Write the Zephyr workbook AND its zfj_import_config.json; return the xlsx
    path.

    The two files are always written together into one folder -- the config JSON
    is not an on-request extra, it is half of the deliverable.

    ``dry_run`` defaults to True -- the SAFE value -- because the column layout
    is not vendor-verified and the tester performs the external write (the
    import into Jira). A dry run emits PILOT_CASE_LIMIT case(s), preferring the
    first multi-step one, under a clearly pilot-named file and folder.

    ``output_dir`` is where the per-export folder is created: callers pass
    ``settings.qa_export_dir`` (or the folder the Excel deliverable landed in)
    so the pair stays with the tester's other artifacts and is never swept.
    ``None`` falls back to the shared 0700 secure temp dir, and an unusable
    directory degrades to that same fallback instead of failing the export.

    Mirrors the peer exporters' contract: raises OSError / ValueError on a
    genuine file-I/O failure only. Every call site in tools/mcp_handlers.py
    wraps this in try/except and degrades to a markdown warning, so a failed
    Zephyr export can never break generation.
    """
    story_key = (story_key or "").strip().upper()
    pairs = assign_external_ids(suite)
    if dry_run:
        keep = set(pilot_indices([tc for _, tc in pairs]))
        emitted = [pair for i, pair in enumerate(pairs) if i in keep]
    else:
        emitted = pairs

    checks = validate_for_zephyr(
        suite, story_key, dry_run=dry_run, emitted_cases=len(emitted)
    )
    for warning in checks.get("warnings", []):
        logger.warning("Zephyr export: %s", warning)
    for note in checks.get("notes", []):
        logger.info("Zephyr export: %s", note)

    target = _export_dir(output_dir, suite, dry_run)
    xlsx_path = str(target / workbook_name(dry_run))
    json_path = str(target / CONFIG_FILENAME)

    workbook = xlsxwriter.Workbook(xlsx_path, {"strings_to_formulas": False})
    row_count = 0
    try:
        header_fmt = workbook.add_format(
            {
                "bold": True,
                "font_size": 16,
                "border": 1,
                "valign": "vcenter",
                "text_wrap": True,
            }
        )
        cell_fmt = workbook.add_format(
            {"border": 1, "valign": "top", "text_wrap": True}
        )
        # An Arabic-majority cell needs reading_order=2, or Excel lays it out
        # left-to-right even though the string itself is correct -- and the
        # tester blames the generator. Mirrors tools/xlsx_generator.py:258-281;
        # without this the Zephyr workbook was the one deliverable that still
        # rendered Arabic LTR once QA_BILINGUAL_RULES was on.
        rtl_cell_fmt = workbook.add_format(
            {
                "border": 1,
                "valign": "top",
                "text_wrap": True,
                "reading_order": 2,
                "align": "right",
            }
        )
        ws = workbook.add_worksheet(SHEET_NAME)
        for i, width in enumerate(_COL_WIDTHS):
            ws.set_column(i, i, width)
        ws.freeze_panes(1, 0)
        ws.write_row(0, 0, _HEADERS, header_fmt)
        ws.set_row(0, _HEADER_ROW_HEIGHT)

        row_idx = 1
        for external_id, tc in emitted:
            for row in case_rows(
                tc,
                external_id,
                story_key=story_key,
                labels=_labels_for(tc, extra_labels),
                epic_link=epic_link,
                fix_versions=fix_versions,
                sprint=sprint,
            ):
                # Per-cell rather than write_row: only the cells that
                # actually carry RTL text get the RTL format. bidi_isolate runs
                # LAST (case_rows already sanitize_cell()'d every cell), which is
                # the sanitize-before-isolate ordering tools/xlsx_generator.py:58
                # documents. It is a verified no-op on pure-ASCII, so the cells
                # the Zephyr importer PARSES -- "Test", "tests", the story key,
                # the External ID -- stay byte-identical and cannot be broken by
                # an invisible isolate mark.
                for col, value in enumerate(row):
                    text = "" if value is None else str(value)
                    ws.write(
                        row_idx,
                        col,
                        bidi_isolate(text),
                        rtl_cell_fmt if is_rtl_cell(text) else cell_fmt,
                    )
                row_idx += 1
        row_count = row_idx - 1
    finally:
        workbook.close()
    _restrict(xlsx_path)

    config = build_import_config(
        suite,
        story_key=story_key,
        validation=checks,
        row_count=row_count,
        dry_run=dry_run,
        id_pairs=emitted,
    )
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2, ensure_ascii=False)
    _restrict(json_path)

    logger.info(
        "Zephyr export written: %s (+ %s) -- %d of %d cases, %d rows, dry_run=%s",
        xlsx_path,
        CONFIG_FILENAME,
        len(emitted),
        len(pairs),
        row_count,
        dry_run,
    )
    return xlsx_path


def cleanup_temp_files(max_age_seconds: int = 3600) -> int:
    """Delete stale zephyr_* export folders in the SECURE TEMP dir. Never raises.

    Mirrors the peer exporters' sweep and, like tools/testrail_exporter.py,
    touches ONLY the app's own 0700 subdirectory -- never the shared temp root,
    where a same-named path could belong to another user.

    SCOPE, stated plainly: this sweeps ``secure_temp_dir()`` and NOTHING else.
    It can never reach a folder written under ``QA_EXPORT_DIR``, which is
    deliberate -- that folder is the tester's permanent deliverable and its
    ``zfj_import_config.json`` must survive until the next re-import. Like every
    other exporter's ``cleanup_temp_files``, no caller is wired to it today; it
    exists for parity and for a future explicit sweep. Reclaiming
    QA_EXPORT_DIR is a documented manual step (operations/runbook.md).
    """
    deleted = 0
    try:
        base = secure_temp_dir()
    except OSError:
        logger.warning("Zephyr cleanup: secure export dir unavailable")
        return 0
    now = time.time()
    for path in base.glob("zephyr_*"):
        try:
            if not path.is_dir():
                continue
            age = now - path.stat().st_mtime
            if age > max_age_seconds:
                shutil.rmtree(path, ignore_errors=True)
                deleted += 1
                logger.info(
                    "Cleaned up stale Zephyr export dir: %s (age %.0fs)", path, age
                )
        except OSError:
            logger.warning("Could not check/delete Zephyr export dir: %s", path)
    if deleted:
        logger.info("Zephyr cleanup: removed %d stale folder(s)", deleted)
    return deleted
