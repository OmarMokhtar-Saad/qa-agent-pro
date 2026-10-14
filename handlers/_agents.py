"""Moved from handlers (refactor P2): _agents."""

from __future__ import annotations

from typing import Any, cast

# The distribution build ships ONLY the test-case pipeline (see QA_DIST_MODE):
# bug-report / exploratory-coach / Maestro modules are absent there, so their
# imports are guarded. mcp_server.py skips registering the excluded tools when
# test_cases_only() is true; the handler gates below are defense in depth.
try:
    # The LEGACY generate_bug_report / coach_next_step are not imported here.
    # They were dropped from this block in host-boomerang Phase 2, when no
    # handler called them any more and graph.py imported them straight from the
    # agent modules; on 2026-08-15 (dead-code deletion Phase 2, batches P2-A and
    # P2-C) graph.py and both coroutines were DELETED outright, so there is now
    # nothing to import even in principle. tools.coach_memory.strip_meta
    # likewise moved inside agents.exploratory_coach_agent.finalize_coach_step.
    from agents.bug_report_agent import (
        BugReportOptions,
        clean_host_report,
        is_bug_report_fallback,
        missing_report_sections,
        prepare_bug_report,
    )
    from agents.exploratory_coach_agent import (
        CoachStepOptions,
        finalize_coach_step,
        prepare_coach_step,
    )
    from tools.coach_memory import (
        MAX_AREA_CHARS,
        MAX_AREAS,
        CoverageTurn,
        create_session_memory,
        update_coverage,
    )

    FULL_EDITION = True
except ImportError:  # pragma: no cover — exercised only in distribution builds
    BugReportOptions = cast("Any", None)  # type: ignore[misc]
    clean_host_report = cast("Any", None)
    is_bug_report_fallback = cast("Any", None)
    missing_report_sections = cast("Any", None)
    prepare_bug_report = cast("Any", None)
    CoachStepOptions = cast("Any", None)  # type: ignore[misc]
    finalize_coach_step = cast("Any", None)
    prepare_coach_step = cast("Any", None)
    create_session_memory = cast("Any", None)
    update_coverage = cast("Any", None)
    CoverageTurn = cast("Any", None)  # type: ignore[misc]
    MAX_AREA_CHARS = cast("Any", None)
    MAX_AREAS = cast("Any", None)

    FULL_EDITION = False
