"""Case cards for the report: per-case facts, screens, planning, card HTML and the cases section."""

from __future__ import annotations

import json  # noqa: F401
import logging
from collections import Counter  # noqa: F401

from tools.mobile import run_store  # noqa: F401
from tools.mobile.rcrd_attrs import _card_attrs, _card_bits, _card_snippet  # noqa: F401
from tools.mobile.rcrd_card import (  # noqa: F401
    _card_body,
    _card_head,
    _card_html,
    _case_card,
)
from tools.mobile.rcrd_cases import _cases_html, _cases_section  # noqa: F401
from tools.mobile.rcrd_facts import (  # noqa: F401
    _case_facts,
    _case_planned,
    _case_planning,
    _case_record,
)
from tools.mobile.rcrd_read import (  # noqa: F401
    _capture_of,
    _case_wall,
    _expected_of,
    _network_of,
)
from tools.mobile.rcrd_screens import _case_screens, _last_looks  # noqa: F401
from tools.mobile.rcrd_steps import _card_steps, _card_tech  # noqa: F401
from tools.mobile.rcrd_toolbar import (  # noqa: F401
    _toolbar,
    _toolbar_groups,
    _toolbar_sorts,
    _verdict_chips,
)
from tools.mobile.rcrd_verdict import _crash_block, _ended_block, _vstrip  # noqa: F401
from tools.mobile.report_frames import _look_pic, _seq_frames  # noqa: F401
from tools.mobile.report_media import _video_html  # noqa: F401
from tools.mobile.report_text import (  # noqa: F401
    LAT_BUCKETS,
    _lat_bucket,
    _slug,
    _stamp,
    _text,
    esc,
    fmt_ms,
)
from tools.mobile.report_trace import _AFTER_LOOK, _step_html, _trace_rows  # noqa: F401
from tools.mobile.report_verdict import (  # noqa: F401
    DONE_VERDICTS,
    TILES,
    _app_logged,
    _planned_case,
    _tone,
    _verdict_of,
    coverage_phrase,
)
from tools.mobile.report_widgets import (  # noqa: F401
    MSEP,
    RAIL,
    _all_chip,
    _chip,
    _filter_group,
    _metric,
    _PageData,
    _pill,
    _sec_block,
)
from tools.mobile_capture import flows as api_flows  # noqa: F401
from tools.mobile_evidence import crash_detector  # noqa: F401
from tools.mobile_evidence import exchanges as ev_exchanges  # noqa: F401
from tools.mobile_evidence import render as ev_render  # noqa: F401

# same logger as the old module; kept for log filters
logger = logging.getLogger("tools.mobile.report")
