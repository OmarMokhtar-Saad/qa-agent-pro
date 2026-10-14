"""Part of the mobile_run split (see handlers/mobile/run.py): cases."""

from __future__ import annotations

from dataclasses import dataclass

import tools.mobile.session_runs as session_runs
from handlers import _common, _elicit
from handlers.mobile.run_hooks import mobile_run
from tools.mobile import importers as mobile_importers
from tools.mobile import render as mobile_render
from tools.untrusted import single_line as _safe


def _mobile_rerun_failures_cases() -> tuple[str, list, dict]:
    """The "rerun_failures" choice: the failed cases of the latest run."""

    runs = (session_runs.list_runs(1) or {}).get("content") or []
    if not runs:
        return _no_previous_run()
    previous = str(runs[0].get("run_id") or "")
    return _rerun_reply(previous, session_runs.load_run_cases(previous))


def _no_previous_run() -> tuple[str, list, dict]:
    """The refusal when there is no earlier run to re-run failures from."""
    return "⚠️ There is no previous run to re-run failures from.", [], {}


def _rerun_reply(previous: str, loaded: dict) -> tuple[str, list, dict]:
    """``(reply, cases, filters)`` for the cases *loaded* from run *previous*."""

    if loaded.get("error"):
        return "⚠️ " + _safe(loaded["error"], 300), [], {}
    return "", list(loaded.get("content") or []), {"failed_last_run": previous}


@dataclass(frozen=True)
class _CaseAsk:
    """The start-menu choice and the arguments naming its cases."""

    picked: str
    suite_id: str
    cases: str
    progress: _common.ProgressCb


async def _mobile_collect_cases(ask: _CaseAsk) -> tuple[str, list, dict]:
    """The cases a start-menu choice names: ``(reply, collected, filters)``.

    A non-empty *reply* is the whole answer (a menu, a refusal); an empty one
    means *collected* and *filters* are ready to plan.
    """

    if ask.picked in ("stored_suite", "current_suite"):
        return await _mobile_suite_cases(ask.suite_id)
    if ask.picked == "own_cases":
        return await _mobile_own_cases(ask.cases, ask.progress)
    if ask.picked == "rerun_failures":
        return _mobile_rerun_failures_cases()
    return mobile_render.source_menu_markdown(), [], {}


async def _mobile_suite_cases(suite_id: str) -> tuple[str, list, dict]:
    """The "stored_suite" / "current_suite" choice: the cases of one suite."""

    # ONE branch for two menu options, deliberately. This server has no
    # notion of "this chat": the suite generated a moment ago is reached by
    # the same suite_id as one from last week, and the recent-suites picker
    # lists it first. Two code paths would only differ in their wording,
    # and would drift.
    if not suite_id:
        return await _elicit.recent_suites_markdown("qa_mobile_test"), [], {}
    loaded = await mobile_run.load_suite(suite_id)
    if loaded.get("error") or loaded.get("content") is None:
        return (
            "\u26a0\ufe0f Could not load suite `" + _safe(suite_id, 80) + "`.",
            [],
            {},
        )
    return "", list(getattr(loaded["content"], "test_cases", []) or []), {}


async def _mobile_own_cases(
    cases: str, progress: _common.ProgressCb
) -> tuple[str, list, dict]:
    """The "own_cases" choice: the cases the tester pasted in."""
    reply, collected, rejections = _import_own_cases(cases)
    if reply:
        return reply, [], {}
    if rejections:
        await _common.emit(progress, "⚠️ Some rows were not imported.")
    return "", collected, {}


_NO_CASE_READ = "⚠️ No case could be read from that, so no run was created.\n\n"


def _import_own_cases(cases: str) -> tuple[str, list, str]:
    """``(reply, collected, rejections)``; a non-empty *reply* is the refusal."""

    imported = session_runs.import_cases(cases)
    if imported.get("error"):
        return "⚠️ " + _safe(imported["error"], 400), [], ""
    body = imported.get("content") or {}
    collected = list(body.get("cases") or [])
    rejections = mobile_importers.render_rejections(body)
    if not collected:
        return _NO_CASE_READ + rejections, [], ""
    return "", collected, rejections


def _mobile_resumable_run(package: str, collected: list) -> dict | None:
    """The earlier run of the SAME cases against this app, if there is one."""

    return session_runs.find_resumable_run(
        session_runs.case_signature(package, collected)
    )
