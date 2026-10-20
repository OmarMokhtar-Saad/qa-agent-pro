"""Journey and results rendering for the report: per-turn rows, goals, issues and the results section."""

from __future__ import annotations

from typing import Any  # noqa: F401

from tools.mobile import run_store  # noqa: F401
from tools.mobile.rcrd_verdict import _crash_block  # noqa: F401
from tools.mobile.report_frames import _SHOTS, LOOK_EXACT, _resolve_look  # noqa: F401
from tools.mobile.report_media import (  # noqa: F401
    _IMAGES,
    THUMB_SIZES,
    _clip_html,
    _img_html,
    _media_key,
    _turn_frame,
)
from tools.mobile.report_shell import MEDIA_DIR  # noqa: F401
from tools.mobile.report_text import _text, esc, fmt_ms  # noqa: F401
from tools.mobile.report_trace import _step_html  # noqa: F401
from tools.mobile.report_verdict import (  # noqa: F401
    DONE_VERDICTS,
    _app_logged,
    _goal_result,
    _unify_verdict,
)
from tools.mobile.report_widgets import (  # noqa: F401
    _PageData,
    _pill,
    _segbar,
    _tile_order,
)
from tools.mobile.rjny_args import _JourneyData, _ResultsIn  # noqa: F401
from tools.mobile.rjny_goals import _goal_meta, _journey_html  # noqa: F401
from tools.mobile.rjny_results import _issues_html, _results_html  # noqa: F401
from tools.mobile.rjny_thumb import _thumb_html  # noqa: F401
from tools.mobile.rjny_turn import (  # noqa: F401
    _turn_extra,
    _turn_html,
    _turn_meta,
    _turn_tech_rows,
)
from tools.mobile.rjny_words import (  # noqa: F401
    RESULT_LABEL,
    RESULT_PILL,
    RESULT_TONE,
    RESULTS,
    SUMMARY_ID,
    _count,
    _finding_of,
    _result_pill,
)
from tools.mobile_evidence import render as ev_render  # noqa: F401
