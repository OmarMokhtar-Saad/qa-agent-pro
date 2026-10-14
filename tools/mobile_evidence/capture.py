"""Facade over the ``capture_*`` modules: the device side of app-log evidence.

Clear, slice, pull -- and scrub before disk. Every name the old module exposed is
re-exported here so ``capture.NAME`` keeps resolving for ``tools.mobile`` and the
tests. The code lives in ``capture_consts`` (every cap and reason string),
``capture_gate`` (the one flag read, plan D3), ``capture_profile``,
``capture_typed`` (typed values per run, memory only), ``capture_records``,
``capture_scrub`` (redaction before disk, plan D4), ``capture_logdev`` (the app-log
lane's one ``adb`` home), ``capture_case`` (``begin_case``, ``slice_case``),
``capture_events`` (``pull_events``), and for the network capture
``capture_sampler``, ``capture_console``, ``capture_pcap``, ``capture_rows``,
``capture_net_begin``, ``capture_net_abort`` and ``capture_net_finish``.

The state dicts are the same objects as in their homes. A scalar cap or a
function bound here is a read-only copy: patch the home module
(``capture_consts``, ``capture_logdev.adb``, ``capture_gate.gate_refusal``), because a
patch only bites in the module whose code reads the name. This module knows no app:
every vocabulary word comes from the profile.
"""

from __future__ import annotations

import asyncio  # noqa: F401
import json  # noqa: F401
import logging
import os  # noqa: F401
import re  # noqa: F401
import time  # noqa: F401
from typing import Any, cast  # noqa: F401

from tools.mobile import adb, run_store  # noqa: F401
from tools.mobile_evidence import crash_detector, netattr, pcap, profiles  # noqa: F401
from tools.mobile_evidence.capture_case import begin_case, slice_case  # noqa: F401
from tools.mobile_evidence.capture_console import (  # noqa: F401
    _app_uid,
    _avd_path,
    _capture_name,
    _capture_start,
)
from tools.mobile_evidence.capture_consts import (  # noqa: F401
    _SEGMENT_NAME_RE,
    _UNSCOPED_GENERIC_REASON,
    ABANDONED_REASON,
    ABANDONED_STAGE,
    CLOCK_NOT_READ,
    EVENTS_FILE,
    EVIDENCE_FLAG_NAME,
    MAX_EVENTS_BYTES,
    MAX_GENERIC_SLICE_BYTES,
    MAX_LIVE_SAMPLERS,
    MAX_NETWORK_ROWS,
    MAX_NETWORK_SAMPLES,
    MAX_REMEMBERED_RUNS,
    MAX_SLICE_BYTES,
    NETWORK_NAME_PREFIX,
    NETWORK_SAMPLE_INTERVAL_S,
    ORPHAN_CAPTURE_TTL_S,
)
from tools.mobile_evidence.capture_events import pull_events  # noqa: F401
from tools.mobile_evidence.capture_gate import _skipped, gate_refusal  # noqa: F401
from tools.mobile_evidence.capture_logdev import (  # noqa: F401
    _dump_slice,
    _read_segments,
    _segment_names,
    _slice_pid,
)
from tools.mobile_evidence.capture_net_abort import abort_network  # noqa: F401
from tools.mobile_evidence.capture_net_args import (  # noqa: F401
    AbortReq,
    BeginReq,
    FinishReq,
)
from tools.mobile_evidence.capture_net_begin import begin_network  # noqa: F401
from tools.mobile_evidence.capture_net_finish import finish_network  # noqa: F401
from tools.mobile_evidence.capture_pcap import (  # noqa: F401
    _pcap_path,
    _read_and_delete,
    _read_pcap,
    _sweep_orphan_captures,
)
from tools.mobile_evidence.capture_profile import (  # noqa: F401
    _is_generic,
    _no_log_dir_reason,
    _profile,
    _profile_label,
    _profile_or_generic,
)
from tools.mobile_evidence.capture_records import (  # noqa: F401
    _events_record,
    _logcat_only,
    _network_record,
    _network_skipped,
    _slice_record,
    _slice_skipped,
)
from tools.mobile_evidence.capture_refs import CaseRef, RunRef, SliceCtx  # noqa: F401
from tools.mobile_evidence.capture_rows import (  # noqa: F401
    _attributed_rows,
    _parsed_record,
    _scrub_rows,
    _write_network_summary,
)
from tools.mobile_evidence.capture_sampler import (  # noqa: F401
    _NET_SAMPLERS,
    _sample_loop,
    _start_sampler,
    _stop_sampler,
)
from tools.mobile_evidence.capture_scrub import (  # noqa: F401
    _scrub_lines,
    _scrub_record,
    _write_slice,
)
from tools.mobile_evidence.capture_typed import (  # noqa: F401
    _TYPED_BY_RUN,
    forget_run,
    remember_typed,
)
from tools.mobile_shared import scrub  # noqa: F401

logger = logging.getLogger(__name__)
