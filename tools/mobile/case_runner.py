"""One test case's lifecycle: clean launch, plan, replay, verdict, checkpoint.

Two invariants this module exists to hold, both mutation-proofed:

1. **``force_stop`` then ``launch`` before EVERY case.** A case that inherits the
   previous case's half-open dialog is not the case the tester wrote, and the
   failure it reports is a lie. Login survives, because ``force-stop`` kills the
   process and not its data.
2. **At most ``MAX_ESCAPES`` boomerangs per case, then ``blocked``.** The escape
   hatch is what makes plan-then-replay workable, and an uncapped escape hatch is
   an infinite loop that spends the tester's tokens. The counter lives in the
   CHECKPOINT, not in memory, so resuming in a fresh chat cannot reset it -- the
   MCP server restarts on every ``.env`` edit and in-memory would mean "escape
   forever, one restart at a time".
"""

from __future__ import annotations

import logging  # noqa: F401
import time  # noqa: F401
from typing import Any, NamedTuple  # noqa: F401

from tools.mobile import actions as actions_mod  # noqa: F401
from tools.mobile import (  # noqa: F401
    adb,
    ime_session,
    media,
    run_store,
    run_verdict,
    step_timing,
)
from tools.mobile import run_packets as mobile_run  # noqa: F401
from tools.mobile.case_run_checkpoint import (  # noqa: F401
    _carried_fields,
    _checkpoint,
    _step_payload,
)
from tools.mobile.case_run_consts import (  # noqa: F401
    BUDGET_CAP_REASON,
    ESCAPE_CAP_REASON,
    MAX_BUDGET_STOPS,
    MAX_CASE_TRACE,
    MAX_ESCAPES,
    MAX_FREE_STOPS,
    MAX_VISUAL_STOPS,
    NEEDS_MODEL,
    NEEDS_TESTER,
    REASON_BUDGET,
    REASON_SELECTOR,
    REASON_VISUAL,
    TEXT_OPS,
    UNCHARGED_CAPS,
    logger,
)
from tools.mobile.case_run_counts import (  # noqa: F401
    _prior_uncharged,
    _uncharged_count,
    budget_stops_used,
    case_view,
    escapes_used,
    free_stops_used,
    uncharged_stops,
)
from tools.mobile.case_run_escape import (  # noqa: F401
    _ask_model_payload,
    _escape_checkpoint,
    _uncharged_reason,
)
from tools.mobile.case_run_evidence import (  # noqa: F401
    _abandon,
    _begin_evidence,
    _finish_wire,
    _splice_evidence,
)
from tools.mobile.case_run_record import (  # noqa: F401
    _evidence_record,
    _merge_crash,
    capture_record,
    network_record,
)
from tools.mobile.case_run_script import (  # noqa: F401
    _fold_queue,
    _parse_submission,
    _ready_keyboard,
    _refused_script,
    note_refusal,
    script_types,
)
from tools.mobile.case_run_slice import _record_slice, _slice_evidence  # noqa: F401
from tools.mobile.case_run_start import (  # noqa: F401
    _settled_screen,
    _wait_for_foreground,
    _write_planning,
    observe_settle,
    start_case,
)
from tools.mobile.case_run_submit import (  # noqa: F401
    _close_step_evidence,
    _replay_recorded,
    submit_case,
)
from tools.mobile.case_run_trace import (  # noqa: F401
    _already_dropped,
    _trim_marker,
    _trim_trace_middle,
    merge_traces,
)
from tools.mobile.case_run_types import (  # noqa: F401
    Submission,
    _Ref,
    _Settle,
    _Step,
)
from tools.mobile.case_run_verdict import (  # noqa: F401
    _crash_override,
    _outcome_checkpoint,
    _read_step,
    _terminal_verdict,
    _tester_payload,
)
from tools.mobile.case_run_wait import (  # noqa: F401
    FOREGROUND_POLL_S,
    FOREGROUND_TIMEOUT_S,
    MAX_FOREGROUND_POLLS,
    _foreground_refusal,
    _sleep,
    _start_refusal,
)
from tools.mobile.providers import composite  # noqa: F401
from tools.mobile.replay.context import (  # noqa: F401
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_NEEDS_TESTER,
    Context,
)
from tools.mobile.replay.dump import dump_raw, resolve_activity  # noqa: F401
from tools.mobile.replay.guard import MAX_GUARD_NODE_CHARS  # noqa: F401
from tools.mobile.replay.replay import replay  # noqa: F401
from tools.mobile.verdicts import (  # noqa: F401
    VERDICT_BLOCKED,
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_UNVERIFIED,
)
from tools.mobile_capture import flows as api_flows  # noqa: F401
from tools.mobile_evidence import capture, crash_detector  # noqa: F401
