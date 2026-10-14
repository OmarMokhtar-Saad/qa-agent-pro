"""The finalize orchestrator: dedupe, score, renumber, report, export."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from agents.scenario_categories import CategoryResult
from agents.scenario_export import _export_files
from agents.scenario_finalize_steps import (
    _attach_rtm_data,
    _dedupe_for_finalize,
    _log_finalize_funnel,
    _renumber_and_normalize,
    _score_and_scope,
)
from agents.scenario_prepared import PreparedGeneration
from agents.scenario_report import _risk_line
from agents.scenario_status import _emit_status
from agents.scenario_summary import (
    _COMPACT_SUMMARY_ORDER,
    _FULL_SUMMARY_ORDER,
    _compose_summary,
    _report_sections,
    _rtm_headline,
    _Scoring,
    _summary_head,
)
from tools.models import TestCase, TestSuite
from tools.risk_scorer import build_risk_section


# _finalize_generation. The progress counter is corrected to the FINAL count:
# each category's on_progress slot reports a running tc_id count from its OWN
# in-flight stream, so the sum shown during generation can overshoot.
# defer_files is compact mode: hand the suite back for on-demand export and
# return a short summary (counts + gaps) without the per-case tables.
@dataclass(frozen=True)
class _FinalizeOptions:
    """Keyword options of ``_finalize_generation`` (an unknown name raises)."""

    on_progress: Callable[[int], Awaitable[None]] | None = None
    on_status: Callable[[str], Awaitable[None]] | None = None
    defer_files: bool = False
    on_suite_ready: Callable[[TestSuite], None] | None = None
    on_report_ready: Callable[[str], None] | None = None
    ui_content: dict | None = None


def _scored_suite(
    prepared: PreparedGeneration, all_cases: list[TestCase]
) -> tuple[TestSuite, _Scoring, str]:
    """Score, renumber and wrap the cases: (suite, scoring, risk section)."""
    scored, risk_section, rule_pack_ctx, out_of_scope_ids = _score_and_scope(
        all_cases, prepared
    )
    renumbered = _renumber_and_normalize(scored)
    suite = TestSuite(test_cases=renumbered)
    _attach_rtm_data(suite, prepared, renumbered)
    risk_section = build_risk_section(renumbered) if risk_section else risk_section
    scoring = _Scoring(renumbered, rule_pack_ctx, out_of_scope_ids)
    return suite, scoring, risk_section


def _finalize_sections(
    prepared: PreparedGeneration, all_cases: list[TestCase]
) -> tuple[TestSuite, dict[str, str]]:
    """The scored suite and every summary section keyed by name."""
    suite, scoring, risk_section = _scored_suite(prepared, all_cases)
    sections = _report_sections(prepared, suite, scoring)
    sections["risk"] = risk_section
    sections["semantic_dedup"] = ""  # semantic_dedup_enabled() is hardcoded False
    _log_finalize_funnel(suite, scoring.renumbered, sections["quality"])
    return suite, sections


def _compact_lines(prepared, suite, parts) -> dict[str, str]:
    """The two compact-only sections: the risk line and the RTM headline."""
    return {
        "risk_line": _risk_line(parts[2]),
        "rtm_line": _rtm_headline(prepared.acs, prepared.source_acs, suite),
    }


def _compact_reply(sections, parts) -> tuple[str, str, str, str, str]:
    """The deferred-files reply: a short summary and no exported files."""
    head, status, _risk_counts = parts
    compact = _compose_summary(head, sections, _COMPACT_SUMMARY_ORDER)
    return compact, "", "", "", status


async def _full_reply(suite, sections, parts) -> tuple[str, str, str, str, str]:
    """The full reply: write the files, then the verbose summary."""
    head, status, _risk_counts = parts
    paths = await _export_files(suite)
    xlsx_path, csv_path, testrail_path, file_note, export_section = paths
    sections["file_note"], sections["export"] = file_note, export_section
    summary = _compose_summary(head, sections, _FULL_SUMMARY_ORDER)
    return summary, xlsx_path, csv_path, testrail_path, status


async def _reply(prepared, built, opts) -> tuple[str, str, str, str, str]:
    """Compact reply when files are deferred, else the full one."""
    suite, sections, parts = built
    if not opts.defer_files:
        return await _full_reply(suite, sections, parts)
    if opts.on_suite_ready is not None:
        opts.on_suite_ready(suite)
    sections.update(_compact_lines(prepared, suite, parts))
    return _compact_reply(sections, parts)


async def _finalize_generation(
    prepared: PreparedGeneration,
    all_cases: list[TestCase],
    category_results: list[CategoryResult],
    **options: Any,
) -> tuple[str, str, str, str, str]:
    """Dedupe, score, renumber, report, export (options: ``_FinalizeOptions``);
    returns (summary, xlsx, csv, testrail, status)."""
    opts = _FinalizeOptions(**options)
    all_cases = _dedupe_for_finalize(all_cases, prepared.source_acs)
    await _emit_status(
        opts.on_status, "📊 Scoring by risk and finalizing the test suite…"
    )
    suite, sections = _finalize_sections(prepared, all_cases)
    if opts.on_progress is not None:
        await opts.on_progress(len(suite.test_cases))
    parts = _summary_head(suite, category_results, prepared)
    return await _reply(prepared, (suite, sections, parts), opts)
