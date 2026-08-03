"""Test-plan artifacts (QA_TEST_PLAN_ARTIFACTS): an AC-Validation report and a
Test Plan / Strategy section for a freshly generated suite.

Both artifacts are opt-in behind ``settings.qa_test_plan_artifacts`` (default
OFF, per the house rule). When OFF the orchestrator returns ``{}`` WITHOUT any
LLM call. When ON it makes AT MOST TWO ``ask_json`` calls (one per artifact) and
returns a dict of plain data the render helpers turn into markdown sections and
XLSX rows.

House rules honoured here:
  * Never raises — every builder degrades to ``{}`` / an empty artifact so a
    failure here can never break generation.
  * LLM access is only via ``llm.ask_json``.
  * All externally-sourced text (feature text, acceptance criteria) is wrapped
    via ``tools.untrusted.wrap_untrusted`` before it reaches the model, and the
    system prompts carry ``_GUARD``.
  * Cell sanitisation for the XLSX sheets happens in ``tools.xlsx_generator``
    (these helpers stay pure and return plain strings).
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from config.settings import settings
from llm import ask_json, server_llm_scope
from tools.untrusted import _GUARD, wrap_untrusted

logger = logging.getLogger(__name__)

# Host-boomerang ledger id covering BOTH ask_json calls in this module (they
# only ever run through build_test_plan_artifacts). The MCP host path no
# longer reaches them, but graph.py, the eval harness and the documented
# rollback -- QA_HOST_TEST_PLAN_REVIEW_ENABLED=false -- still do, and an
# UNTAGGED call is refused once QA_SERVER_LLM_ENABLED is false.
_LEDGER_ID = "test_plan_report.build"


# --------------------------------------------------------------------------- #
# Structured response models (used only as ask_json response_models)
# --------------------------------------------------------------------------- #


class _AcVerdict(BaseModel):
    ac_id: str = Field(
        default="", description="The acceptance-criterion id, e.g. AC-001"
    )
    summary: str = Field(default="", description="A short restatement of the AC")
    testable: bool = Field(
        default=False, description="Can this AC be verified by a manual test?"
    )
    unambiguous: bool = Field(
        default=False, description="Is the AC free of vague/ambiguous wording?"
    )
    independent: bool = Field(
        default=False,
        description="Can it be validated on its own, without depending on another AC?",
    )
    notes: str = Field(default="", description="Short justification / caveat")


class _AcValidation(BaseModel):
    verdicts: list[_AcVerdict] = Field(default_factory=list)
    open_questions: list[str] = Field(
        default_factory=list,
        description="Clarifying questions the ACs still leave open",
    )
    missing_scenarios: list[str] = Field(
        default_factory=list,
        description="Scenarios implied by the ACs that appear untested",
    )


class _TestPlan(BaseModel):
    scope_in: list[str] = Field(default_factory=list, description="What IS covered")
    scope_out: list[str] = Field(
        default_factory=list, description="What is NOT covered"
    )
    test_levels: list[str] = Field(
        default_factory=list, description="e.g. unit / integration / system / UAT"
    )
    environment: list[str] = Field(
        default_factory=list, description="Environment(s) the tests need"
    )
    test_data: list[str] = Field(
        default_factory=list, description="Test-data needs / fixtures"
    )
    entry_criteria: list[str] = Field(default_factory=list)
    exit_criteria: list[str] = Field(default_factory=list)
    techniques: list[str] = Field(
        default_factory=list,
        description="Applicable techniques: equivalence partitioning, boundary "
        "value analysis, decision tables, etc.",
    )


_AC_SYSTEM = """\
You are a senior QA analyst validating the QUALITY of a feature's acceptance
criteria (ACs) before sign-off. For EACH acceptance criterion given, judge:
- testable: can a manual tester verify it with a concrete, observable outcome?
- unambiguous: is it free of vague wording ("fast", "nice", "properly")?
- independent: can it be validated on its own, not entangled with another AC?
Add a one-line note per AC justifying the verdict.

Then, across the whole set:
- open_questions: up to 6 concrete questions the ACs still leave unanswered.
- missing_scenarios: up to 10 concrete scenarios the ACs imply but do not
  explicitly state (error paths, boundaries, permissions, etc.).

Judge ONLY the acceptance criteria provided as data — do not invent new
requirements. Output STRICTLY the JSON object for the schema, nothing else.
"""

_PLAN_SYSTEM = """\
You are a senior QA lead writing a concise, professional Test Plan / Strategy
for a manual testing team, to attach to the ticket. Ground it ONLY in the
feature description, the generated-suite statistics, and any acceptance criteria
provided. Fill each field with short, concrete bullet phrases (not paragraphs):
- scope_in / scope_out: what this test effort does and does not cover.
- test_levels: e.g. Smoke, Functional/System, Integration, Regression, UAT.
- environment: environment(s), builds, devices/browsers needed.
- test_data: the test-data / fixtures / accounts required.
- entry_criteria / exit_criteria: when testing may start / is considered done.
- techniques: the applicable design techniques — equivalence partitioning,
  boundary value analysis, decision tables, state transitions — matched to this
  feature.
Do not invent requirements not implied by the inputs. Output STRICTLY the JSON
object for the schema, nothing else.
"""


# --------------------------------------------------------------------------- #
# Builders (never raise; degrade to {})
# --------------------------------------------------------------------------- #


def _ac_block(source_acs) -> str:
    """Join REAL source ACs into a single text block (empty when none)."""
    lines = [
        f"{ac.ac_id}: {ac.description}"
        for ac in (source_acs or [])
        if getattr(ac, "ac_id", "") and getattr(ac, "description", "")
    ]
    return "\n".join(lines)


async def build_ac_validation(
    source_acs, feature_text: str, open_questions=None
) -> dict:
    """Per-AC verdict table over the REAL, source-parsed acceptance criteria.

    Returns ``{}`` (with NO LLM call) when there are no source ACs — synthesized
    ACs are not ground truth, so there is nothing to validate. When
    ``open_questions`` is supplied (a requirement_analyzer result already
    computed upstream) it is PREFERRED over the model's own guesses, so the gate
    pass is reused rather than re-run. Never raises — returns ``{}`` on any
    failure.
    """
    try:
        if not source_acs:
            return {}
        ac_block = _ac_block(source_acs)
        if not ac_block.strip():
            return {}
        user = (
            "Feature under test:\n"
            + wrap_untrusted("feature_description", feature_text or "")
            + "\n\nAcceptance criteria to validate:\n"
            + wrap_untrusted("acceptance_criteria", ac_block)
        )
        result: _AcValidation = await ask_json(
            system=_AC_SYSTEM + _GUARD,
            user=user,
            response_model=_AcValidation,
            model=settings.qa_classifier_model or None,
        )
        questions = (
            [q for q in open_questions if q]
            if open_questions
            else list(result.open_questions)
        )
        return {
            "verdicts": [v.model_dump() for v in result.verdicts],
            "open_questions": questions[:10],
            "missing_scenarios": list(result.missing_scenarios)[:15],
            "ac_count": len(source_acs),
        }
    except Exception:
        logger.warning(
            "build_ac_validation failed — omitting AC-validation report",
            exc_info=True,
        )
        return {}


async def build_test_plan(
    feature_text: str, suite_stats: dict, source_acs=None
) -> dict:
    """Test Plan / Strategy grounded on the feature text + suite statistics.

    Runs regardless of whether source ACs exist (the plan is about the effort,
    not AC quality). Returns ``{}`` on any failure or an all-empty plan. Never
    raises.
    """
    try:
        stats_line = ", ".join(f"{k}: {v}" for k, v in (suite_stats or {}).items())
        ac_block = _ac_block(source_acs)
        ac_part = (
            "\n\nAcceptance criteria:\n"
            + wrap_untrusted("acceptance_criteria", ac_block)
            if ac_block.strip()
            else ""
        )
        user = (
            "Feature under test:\n"
            + wrap_untrusted("feature_description", feature_text or "")
            + f"\n\nGenerated suite statistics: {stats_line}"
            + ac_part
        )
        result: _TestPlan = await ask_json(
            system=_PLAN_SYSTEM + _GUARD,
            user=user,
            response_model=_TestPlan,
            model=settings.qa_classifier_model or None,
        )
        plan = result.model_dump()
        if not any(plan.values()):
            return {}
        return plan
    except Exception:
        logger.warning(
            "build_test_plan failed — omitting Test Plan section", exc_info=True
        )
        return {}


async def build_test_plan_artifacts(
    feature_text: str,
    suite_stats: dict,
    source_acs=None,
    open_questions=None,
) -> dict:
    """Orchestrate both artifacts, gated by ``settings.qa_test_plan_artifacts``.

    OFF -> returns ``{}`` with ZERO LLM calls (the flag-off fast path). ON -> at
    most two concurrent ``ask_json`` calls (one is skipped when there are no
    source ACs). Never raises.
    """
    try:
        if not settings.qa_test_plan_artifacts:
            return {}
        # Ledger rule 4: entered BEFORE gather schedules the two tasks, which
        # copy this context at creation, so both inner calls are tagged.
        with server_llm_scope(_LEDGER_ID):
            ac_result, plan_result = await asyncio.gather(
                build_ac_validation(source_acs or [], feature_text, open_questions),
                build_test_plan(feature_text, suite_stats, source_acs),
            )
        artifacts: dict = {}
        if ac_result:
            artifacts["ac_validation"] = ac_result
        if plan_result:
            artifacts["test_plan"] = plan_result
        return artifacts
    except Exception:
        logger.warning("build_test_plan_artifacts failed — omitting", exc_info=True)
        return {}


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
