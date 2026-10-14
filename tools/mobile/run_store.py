"""Per-run state on disk: manifest, per-case checkpoints, and the lease.

This module is the public facade: the implementation lives in the flat
``tools/mobile/run_*.py`` modules (constants, io, redact, manifest, lease,
screens, observations, shots, evidence, gc, list, ...), and every name callers
or tests reach through here is re-exported from its home. A test that patches
a name patches it on the module whose code looks it up at call time.

The design notes (atomic writes, the screen and observation libraries,
write-time redaction, the injected clock, where mutual exclusion lives) are
the docstrings of the owning modules.

**Redaction at the WRITE, not at the reader.** :func:`redact` runs inside every
write path and masks any value under a key in ``SECRET_VALUE_KEYS`` inside an
object marked ``secret: true``, and any value under a key in
``ALWAYS_MASKED_KEYS`` wherever it appears.

It is still not a value scrubber: a credential stored under an unrecognised key
name with no marker WILL reach disk, so a new writer of tester-supplied data
must mark it. A frame in ``shots/`` is pixels and is stored RAW.
"""

from __future__ import annotations

import json  # noqa: F401
import logging
import math  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import shutil  # noqa: F401
import time  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any  # noqa: F401

from tools.mobile import explore_stop, paths  # noqa: F401
from tools.mobile.run_cases import list_cases, read_case, write_case  # noqa: F401
from tools.mobile.run_consts import (  # noqa: F401
    _EVIDENCE_NAME_RE,
    _RUN_ID_RE,
    _RUN_ID_SHAPE_RE,
    _SESSION_RE,
    _TC_ID_RE,
    ALWAYS_MASKED_KEYS,
    BRIEFING_FILE,
    CASES_DIR,
    EVIDENCE_DIR,
    HELD,
    LEASE_FILE,
    LEASE_STALE_S,
    MANIFEST_FILE,
    MAX_RESOLVED_APPS,
    MAX_RUN_OBSERVATIONS,
    MAX_RUN_SHOTS,
    NONE,
    OBSERVATIONS_DIR,
    RESOLVED_APP_TTL_S,
    RESOLVED_APPS_FILE,
    SCREEN_MEMO_FILE,
    SCREENS_DIR,
    SECRET_MASK,
    SECRET_VALUE_KEYS,
    SHOTS_DIR,
    STALE_RUN_S,
    TAKEN_OVER,
)
from tools.mobile.run_coverage import (  # noqa: F401
    DONE_VERDICTS,
    _expected_verdicts,
    _has_verdict,
    coverage_phrase,
    verdict_coverage,
)
from tools.mobile.run_evidence import (  # noqa: F401
    EvidenceRef,
    _evidence_path,
    list_evidence,
    read_evidence_text,
    write_evidence_json,
    write_evidence_text,
)
from tools.mobile.run_gc import (  # noqa: F401
    _keep_reason,
    _run_age,
    _sweep_runs,
    gc_stale_runs,
)
from tools.mobile.run_io import (  # noqa: F401
    _now,
    _read_json,
    _write_bytes,
    _write_json,
    _write_text,
    looks_like_a_run_id,
    run_path,
    valid_run_id,
    valid_tc_id,
)
from tools.mobile.run_lease import (  # noqa: F401
    acquire_lease,
    lease_status,
    takeover_message,
    touch_lease,
)
from tools.mobile.run_lease_core import (  # noqa: F401
    LeaseOpts,
    _lease_answer,
    _lease_decision,
    _lease_input_error,
    _lease_path,
    _swap_lease,
    read_lease,
)
from tools.mobile.run_list import last_used_package, list_runs  # noqa: F401
from tools.mobile.run_manifest import (  # noqa: F401
    append_step_timing,
    create_run,
    read_manifest,
    write_manifest,
)
from tools.mobile.run_memo import (  # noqa: F401
    ScreenMemo,
    _briefing_path,
    _screen_memo_path,
    briefed_session,
    clear_briefing,
    clear_screen,
    last_screen,
    mark_briefed,
    mark_screen,
)
from tools.mobile.run_observations import (  # noqa: F401
    _write_observation,
    list_observations,
    observation_id,
    observation_key,
)
from tools.mobile.run_redact import _is_secret, redact  # noqa: F401
from tools.mobile.run_resolved import (  # noqa: F401
    ResolvedApp,
    _resolved_app_key,
    read_resolved_app,
    write_resolved_app,
)
from tools.mobile.run_resume import resume_point  # noqa: F401
from tools.mobile.run_screens import (  # noqa: F401
    latest_screen,
    list_screens,
    write_screen,
)
from tools.mobile.run_shots import _shot_refusal, read_shot, write_shot  # noqa: F401

logger = logging.getLogger(__name__)
