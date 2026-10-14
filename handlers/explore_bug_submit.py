"""SUBMIT half of the chat-only bug report (explore_bug leaf)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from handlers import _agents, _common
from handlers.explore_bug_task import HostTaskSpec, shape_host_task
from tools import host_llm as _host_llm
from tools.rag_store import add_to_corpus

logger = logging.getLogger("tools.mcp_handlers")

# --------------------------------------------------------------------------- #
# Chat-only ("boomerang") SUBMIT handlers for the two standalone agents.
#
# qa_bug_report / qa_explore_step now PREPARE a tools/host_llm.py task; these
# close it. The host's text is UNTRUSTED and model-derived (close_task tags and
# caps it) and the SERVER still performs every side effect: section validation,
# RAG corpus seeding, coach-memory updates and the audit row. Neither half can
# reach an llm.py backend, so both tools work on a keyless install.
# --------------------------------------------------------------------------- #

# First submission plus ONE structured resubmit round -- the same budget the
# legacy single retry had. A still-malformed final round is returned to the
# tester anyway (never discard their content) but is withheld from the corpus.
_MAX_BUG_REPORT_ROUNDS = 2


@dataclass(frozen=True)
class BugReportRound:
    """What one submitted bug-report round told us."""

    task_id: str
    description: str
    round_no: int
    missing: tuple[str, ...]


def input_error(task_id: str, report: str) -> str | None:
    """The early-return text for a missing task_id or report, else None."""
    if not task_id:
        return "⚠️ Pass the `task_id` from `qa_bug_report`."
    if not (report or "").strip():
        return "⚠️ Send the bug report you wrote as `report`."
    return None


def _read_round(task_id: str, closed: dict) -> tuple[BugReportRound, str]:
    """The round facts and the cleaned report text of a closed task."""
    content = closed.get("content") or {}
    meta = content.get("meta") or {}
    text = _agents.clean_host_report(str(content.get("raw") or ""))
    missing = _agents.missing_report_sections(text)
    round_no = int(meta.get("round") or 1)
    description = str(meta.get("description") or "")
    return BugReportRound(task_id, description, round_no, tuple(missing)), text


async def submit_bug_report(
    task_id: str, report: str, progress: _common.ProgressCb
) -> str:
    """Close the bug-report task, resubmit once if sections are missing, finish."""

    await _common.emit(progress, "🐛 Checking the bug report…")
    closed = await _host_llm.close_task(task_id, report, expect_kind="bug_report")
    if closed.get("error"):
        return (
            f"⚠️ {closed['error']}. Start again with `qa_bug_report` — "
            "a task id is one-shot and expires with the prep TTL."
        )
    rnd, text = _read_round(task_id, closed)
    if rnd.missing and rnd.round_no < _MAX_BUG_REPORT_ROUNDS and rnd.description:
        resubmit = await _resubmit_prompt(rnd)
        if resubmit is not None:
            return resubmit
    return await _finish(rnd, text)


def _resubmit_text(missing: tuple[str, ...], reopened_content: dict) -> str:
    """The missing-section note followed by the reopened round's host task."""
    spec = HostTaskSpec(
        "Bug report — resubmit (round 2 of 2)",
        str(reopened_content.get("task_id") or ""),
        "qa_submit_bug_report",
        "field `report`",
    )
    return (
        "> ⚠️ Missing required section(s): "
        + ", ".join(missing)
        + ". Re-emit the FULL report with those headers and submit it "
        "against the NEW task id below.\n\n"
        + shape_host_task(spec, reopened_content.get("envelope") or {})
    )


async def _resubmit_prompt(rnd: BugReportRound) -> str | None:
    """The resubmit prompt for a report missing sections, or None if no round opened."""
    reopened = await _agents.prepare_bug_report(
        rnd.description,
        _agents.BugReportOptions(round_no=rnd.round_no + 1, missing=rnd.missing),
    )
    reopened_content = reopened.get("content") or {}
    if not reopened.get("error") and reopened_content.get("task_id"):
        return _resubmit_text(rnd.missing, reopened_content)
    logger.warning(
        "could not open a resubmit round for bug-report task %s — "
        "returning the host's text as-is",
        rnd.task_id,
    )
    return None


async def _finish(rnd: BugReportRound, text: str) -> str:
    """Seed the corpus when the report is whole, audit, and shape the reply."""
    # QW-8 discipline, unchanged: never seed the corpus with a degraded
    # report (the fallback sentinel, or one missing required sections).
    if not rnd.missing and (
        _agents.is_bug_report_fallback is None
        or not _agents.is_bug_report_fallback(text)
    ):
        await add_to_corpus("bug_report", text, {"description": rnd.description[:200]})
    await _common.audit(
        "mcp_bug_report",
        entity_id=rnd.task_id,
        detail={"missing_sections": len(rnd.missing), "round": rnd.round_no},
    )
    return _degraded_text(rnd.missing, text) if rnd.missing else text


def _degraded_text(missing: tuple[str, ...], text: str) -> str:
    """The report prefixed with the note that it was not saved to the corpus."""
    return (
        "> ⚠️ This report is still missing "
        + ", ".join(missing)
        + " — it is shown below but was NOT saved to the corpus.\n\n"
        + text
    )
