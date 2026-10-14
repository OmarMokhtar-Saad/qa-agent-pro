"""The app-evidence fragments of the mobile report: a facade over ``render_*``.

Every name ``tools.mobile``, the tests and the dist look up on this module is
re-exported here. Callers resolve these names at call time, so a patch on this
module still bites; ``MAX_LOAD_BYTES`` lives in ``render_esc`` and is patched there.
"""

from __future__ import annotations

from tools.mobile_evidence.render_apis import apis_section
from tools.mobile_evidence.render_capture import (
    CAPTURE_LIMITS_NOTE,
    CAPTURE_TIER_LABELS,
)
from tools.mobile_evidence.render_capture_view import capture_section, case_capture
from tools.mobile_evidence.render_card import (
    card_data,
    card_metrics,
    extra_sorts,
    toolbar_groups,
)
from tools.mobile_evidence.render_esc import (
    CLIP,
    EVENTS_FILE,
    FLAG_OFF_REASON,
    MAX_REPLY_TEXT,
    MAX_ROWS,
    MSEP,
    SERIES,
    ev_esc,
)
from tools.mobile_evidence.render_load import load_run_evidence, release
from tools.mobile_evidence.render_net import NO_RECORD, NOT_STOPPED
from tools.mobile_evidence.render_net_case import case_network
from tools.mobile_evidence.render_net_run import network_rows, network_section
from tools.mobile_evidence.render_perf import perf_extra
from tools.mobile_evidence.render_read import Run
from tools.mobile_evidence.render_run import facts_cells, overview_tiles
from tools.mobile_evidence.render_seq import Trace, sequence_items
from tools.mobile_evidence.render_session import session_block
from tools.mobile_evidence.render_trust import trust_block
from tools.mobile_evidence.render_turns import turns_table

__all__ = [
    "CAPTURE_LIMITS_NOTE",
    "CAPTURE_TIER_LABELS",
    "CLIP",
    "EVENTS_FILE",
    "FLAG_OFF_REASON",
    "MAX_REPLY_TEXT",
    "MAX_ROWS",
    "MSEP",
    "NOT_STOPPED",
    "NO_RECORD",
    "Run",
    "SERIES",
    "Trace",
    "apis_section",
    "capture_section",
    "card_data",
    "card_metrics",
    "case_capture",
    "case_network",
    "ev_esc",
    "extra_sorts",
    "facts_cells",
    "load_run_evidence",
    "network_rows",
    "network_section",
    "overview_tiles",
    "perf_extra",
    "release",
    "sequence_items",
    "session_block",
    "toolbar_groups",
    "trust_block",
    "turns_table",
]
