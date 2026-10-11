"""Moved from handlers (refactor P2): _agents."""

from __future__ import annotations

# The distribution build ships ONLY the test-case pipeline (see QA_DIST_MODE):
# bug-report / exploratory-coach / Maestro modules are absent there, so their
# imports are guarded. mcp_server.py skips registering the excluded tools when
# _test_cases_only() is true; the handler gates below are defense in depth.
try:
    # The LEGACY generate_bug_report / coach_next_step are not imported here.
    # They were dropped from this block in host-boomerang Phase 2, when no
    # handler called them any more and graph.py imported them straight from the
    # agent modules; on 2026-08-15 (dead-code deletion Phase 2, batches P2-A and
    # P2-C) graph.py and both coroutines were DELETED outright, so there is now
    # nothing to import even in principle. tools.coach_memory.strip_meta
    # likewise moved inside agents.exploratory_coach_agent.finalize_coach_step.
    from agents.bug_report_agent import (
        clean_host_report,
        is_bug_report_fallback,
        missing_report_sections,
        prepare_bug_report,
    )
    from agents.exploratory_coach_agent import (
        finalize_coach_step,
        prepare_coach_step,
    )
    from tools.coach_memory import (
        create_session_memory,
        update_coverage,
    )

    _FULL_EDITION = True
except ImportError:  # pragma: no cover — exercised only in distribution builds
    clean_host_report = None
    is_bug_report_fallback = None
    missing_report_sections = None
    prepare_bug_report = None
    finalize_coach_step = None
    prepare_coach_step = None
    create_session_memory = None
    update_coverage = None

    _FULL_EDITION = False
