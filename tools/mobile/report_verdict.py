"""Verdict vocabulary and derivation for the run report: tones, tallies, goals and error classes."""

from __future__ import annotations

from tools.mobile.rvrd_errors import error_classes, explore_stop  # noqa: F401
from tools.mobile.rvrd_goals import (  # noqa: F401
    EXPLORATORY,
    EXPLORE_GOAL_PREFIX,
    _case_type,
    _derive_goals,
    _goal_of,
    _goal_result,
    _planned_case,
    _run_goals,
    _run_kind,
)
from tools.mobile.rvrd_tone import (  # noqa: F401
    _OTHER_TONE,
    DONE_VERDICTS,
    TILES,
    VERDICT_TONE,
    _app_logged,
    _tone,
    _unify_verdict,
    _verdict_of,
    coverage_phrase,
    error_phrase,
    tally,
    verdict_coverage,
)
