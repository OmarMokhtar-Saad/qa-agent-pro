"""Render helpers for the test-plan artifacts: an AC-Validation report and a
Test Plan / Strategy section.

The two ``ask_json`` builders that produced these artifacts server-side were
deleted on 2026-08-16 (dead-code deletion P2-F3); what remains is pure and
model-free. The artifacts themselves now only ever arrive from the HOST, via
``agents/host_mode``'s ``TEST_PLAN_JOB`` validator, which rebuilds them
key-by-key into exactly the shape these helpers consume.

House rules honoured here:
  * Never raises -- every helper degrades to ``""`` / ``[]`` so a failure here
    can never break generation.
  * Cell sanitisation for the XLSX sheets happens in ``tools.xlsx_generator``
    (these helpers stay pure and return plain strings).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# The server-side half of this module -- the `_AcVerdict` / `_AcValidation` /
# `_TestPlan` response models, the `_AC_SYSTEM` / `_PLAN_SYSTEM` prompts,
# `_ac_block`, `build_ac_validation`, `build_test_plan`, the
# `test_plan_artifacts_enabled()` seam and `build_test_plan_artifacts` -- was
# DELETED on 2026-08-16 (dead-code deletion P2-F3), together with the
# `_LEDGER_ID` constant and this module's `from llm import ...`. It held the
# last two `ask_json` calls outside `tools/rtm.py`.
#
# It was dead twice over: the seam was a `False` constant (batch 8b-ii,
# 2026-08-14), and the only other way in was `_finalize_generation`'s
# `test_plan_artifacts_enabled() or host_test_plan is not None`, whose second
# arm is fed by `mcp_handlers._plan_job`, a hardcoded `False`. The ledger id
# "test_plan_report.build" STAYS in `host_llm.LEDGER_IDS` -- see
# docs/LLM_MIGRATION_INVENTORY.md.

# --------------------------------------------------------------------------- #
# Render helpers (pure — markdown for the summary, rows for the XLSX sheets)
# --------------------------------------------------------------------------- #

_PLAN_SECTIONS = [
    ("In Scope", "scope_in"),
    ("Out of Scope", "scope_out"),
    ("Test Levels", "test_levels"),
    ("Environment", "environment"),
    ("Test Data Needs", "test_data"),
    ("Entry Criteria", "entry_criteria"),
    ("Exit Criteria", "exit_criteria"),
    ("Techniques", "techniques"),
]


def _yn(value) -> str:
    return "Yes" if value else "No"


def _md(text) -> str:
    """Flatten a value for a markdown table cell (escape pipes, drop newlines)."""
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def render_markdown(artifacts: dict) -> str:
    """Render both artifacts as markdown sections for the generation summary.

    Returns ``""`` when there is nothing to show. The result starts with a
    leading blank-line separator so it slots into the summary f-string next to
    the other sections. Never raises.
    """
    try:
        if not artifacts:
            return ""
        sections: list[str] = []

        ac = artifacts.get("ac_validation") or {}
        if ac:
            lines = ["## AC Validation Report", ""]
            verdicts = ac.get("verdicts") or []
            if verdicts:
                lines.append(
                    "| AC | Summary | Testable | Unambiguous | Independent | Notes |"
                )
                lines.append("|---|---|---|---|---|---|")
                for v in verdicts:
                    lines.append(
                        f"| {_md(v.get('ac_id', ''))} | {_md(v.get('summary', ''))} | "
                        f"{_yn(v.get('testable'))} | {_yn(v.get('unambiguous'))} | "
                        f"{_yn(v.get('independent'))} | {_md(v.get('notes', ''))} |"
                    )
            questions = ac.get("open_questions") or []
            if questions:
                lines += ["", "**Open questions:**"] + [
                    f"- {_md(q)}" for q in questions
                ]
            missing = ac.get("missing_scenarios") or []
            if missing:
                lines += ["", "**Missing scenarios:**"] + [
                    f"- {_md(m)}" for m in missing
                ]
            sections.append("\n".join(lines))

        plan = artifacts.get("test_plan") or {}
        if plan:
            lines = ["## Test Plan / Strategy", ""]
            for label, key in _PLAN_SECTIONS:
                vals = plan.get(key) or []
                if vals:
                    lines.append(f"**{label}:**")
                    lines += [f"- {_md(x)}" for x in vals]
                    lines.append("")
            sections.append("\n".join(lines).rstrip())

        body = "\n\n".join(s for s in sections if s.strip())
        return f"\n\n{body}" if body else ""
    except Exception:
        logger.warning("render_markdown failed — omitting section", exc_info=True)
        return ""


def ac_validation_rows(artifacts: dict) -> list:
    """Rows (header first) for the 'AC Validation' XLSX sheet, or ``[]``.

    Pure — cell sanitisation is applied by the XLSX writer. Never raises.
    """
    try:
        ac = (artifacts or {}).get("ac_validation") or {}
        if not ac:
            return []
        rows: list = [
            ["AC ID", "Summary", "Testable", "Unambiguous", "Independent", "Notes"]
        ]
        for v in ac.get("verdicts") or []:
            rows.append(
                [
                    str(v.get("ac_id", "")),
                    str(v.get("summary", "")),
                    _yn(v.get("testable")),
                    _yn(v.get("unambiguous")),
                    _yn(v.get("independent")),
                    str(v.get("notes", "")),
                ]
            )
        questions = ac.get("open_questions") or []
        if questions:
            rows.append(["", "", "", "", "", ""])
            rows.append(["Open Questions", "", "", "", "", ""])
            rows += [["", str(q), "", "", "", ""] for q in questions]
        missing = ac.get("missing_scenarios") or []
        if missing:
            rows.append(["", "", "", "", "", ""])
            rows.append(["Missing Scenarios", "", "", "", "", ""])
            rows += [["", str(m), "", "", "", ""] for m in missing]
        return rows
    except Exception:
        logger.warning("ac_validation_rows failed — omitting sheet", exc_info=True)
        return []


def plan_rows(artifacts: dict) -> list:
    """Rows (header first) for the 'Test Plan' XLSX sheet, or ``[]``. Never raises."""
    try:
        plan = (artifacts or {}).get("test_plan") or {}
        if not plan:
            return []
        rows: list = [["Section", "Details"]]
        for label, key in _PLAN_SECTIONS:
            vals = plan.get(key) or []
            details = "\n".join(f"- {x}" for x in vals) if vals else "—"
            rows.append([label, details])
        return rows
    except Exception:
        logger.warning("test_plan_rows failed — omitting sheet", exc_info=True)
        return []
