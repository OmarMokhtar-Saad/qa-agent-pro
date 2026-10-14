"""The standalone HTML run report: a thin facade over the ``rpt_*`` leaves.

``render(run_id)`` is the one entry point (``rpt_run``). The module docstring
that used to live here now sits at the top of ``rpt_ids``, which also owns the
section ids. Every other name here is re-exported because a test, the dist or
another module reads or patches it as ``tools.mobile.report.<name>``.
"""

from __future__ import annotations

import os  # noqa: F401

from tools.mobile import (  # noqa: F401
    actions as actions_mod,
)
from tools.mobile.report_cards import (  # noqa: F401
    _card_html,
    _case_facts,
    _crash_block,
    _vstrip,
)
from tools.mobile.report_diagnostics import (  # noqa: F401
    DEVICE_KIND_UNKNOWN,
    DEVICE_KINDS,
    SYSTEM_IMAGE_ABSENT,
    _a11y_section,
    _device_sub,
    _diag_part,
    _env_section,
    _facts_strip,
    _findings_section,
    _logs_section,
    _timings_part,
)
from tools.mobile.report_frames import (  # noqa: F401
    _SHOTS,
    AMBIGUOUS_LOOK,
    LOOK_EXACT,
    MAX_FRAME_H,
    MAX_FRAME_W,
    NOT_CAPTURED,
    SHOT_MISSING_NOTE,
    SHOT_SRC,
    _device_size,
    _frame_box,
    _frame_html,
    _look_is_ambiguous,
    _phone_html,
    _seq_frames,
    _shot_html,
    _shots,
    scale_bounds,
    wireframe,
)
from tools.mobile.report_journey import (  # noqa: F401
    RESULTS,
    SUMMARY_ID,
    _results_html,
)
from tools.mobile.report_media import (  # noqa: F401
    _IMAGES,
    CLIP_SCALE_NOTE,
    _media_key,
    _media_map,
    _media_src,
    _video_html,
)
from tools.mobile.report_shell import (  # noqa: F401
    _SCRIPT_CLASSES,
    INDEX_FILE,
    MEDIA_DIR,
    REPORT_DIR,
    SHELL,
    SHELL_PATH,
    SHELL_SCRIPT,
    SHELL_STYLE,
    _between,
    _compact_css,
    _compact_js,
    _css_rules,
    _fill,
    _page_style,
    _read_shell,
    report_path,
)
from tools.mobile.report_text import (  # noqa: F401
    _SECRET_MIN,
    _SECRETS,
    LAST_CHECKPOINT_LABEL,
    LAST_CHECKPOINT_SUB,
    MAX_TEXT,
    _ms,
    _short_stamp,
    _stamp,
    elapsed_note,
    esc,
    fmt_ms,
    last_checkpoint,
)
from tools.mobile.report_trace import (  # noqa: F401
    OUTCOME_WORDS,
    _action_line,
    _obs_key,
    _plain_action,
    _step_html,
    _trace_rows,
)
from tools.mobile.report_verdict import (  # noqa: F401
    DONE_VERDICTS,
    EXPLORE_GOAL_PREFIX,
    TILES,
    _derive_goals,
    _goal_result,
    _run_kind,
    _unify_verdict,
    _verdict_of,
    error_classes,
    error_phrase,
    explore_stop,
    tally,
)
from tools.mobile.report_widgets import (  # noqa: F401
    _PageData,
    _sechead,
    _segbar,
)
from tools.mobile.rpt_build import (  # noqa: F401
    _document,
)
from tools.mobile.rpt_ids import (  # noqa: F401
    ABOUT_ID,
    CASES_ID,
    DIAG_ID,
    END_ID,
    ISSUES_ID,
    JOURNEY_ID,
    KNOWLEDGE_ID,
)
from tools.mobile.rpt_input import (  # noqa: F401
    _DocInput,
    _merged_library,
)
from tools.mobile.rpt_meta import (  # noqa: F401
    _meta_html,
)
from tools.mobile.rpt_parts import (  # noqa: F401
    _knowledge_html,
)
from tools.mobile.rpt_run import (  # noqa: F401
    render,
)
from tools.mobile_evidence import (  # noqa: F401
    exchanges as ev_exchanges,
)
from tools.mobile_evidence import (  # noqa: F401
    render as ev_render,
)

#: The page-size pin, shared with ``report_selfcheck`` so the two cannot drift.
#:
#: The index is ONE file inside a self-contained folder: the pictures live in
#: ``../shots/`` and the recordings in ``report/media/``, so nothing on this page
#: is an inlined payload any more. What this bounds is the MARKUP -- a run with
#: thousands of steps grows its case tables without any limit of their own, and a
#: document a browser cannot open is a report the tester cannot read at all.
#: That, and not the pictures, is the surviving constraint.
MAX_PAGE_BYTES = 8 * 1024 * 1024
