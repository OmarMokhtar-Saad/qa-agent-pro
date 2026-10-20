"""Pixels of the run: one clip around each replayed step, one frame after it.

The pruned uiautomator dump describes a screen in TEXT, and the report has only
ever drawn that text back as a wireframe. This module stores what the device
actually showed: an mp4 recorded around each replayed script, and a PNG of the
screen that script left behind.

**One clip per SUBMITTED STEP, not per case.** ``actions.SUBMIT_BUDGET_MS`` is
40 seconds, so one replay can never approach the ~180 second ceiling
``screenrecord`` enforces on the device itself. Recording per replay therefore
needs no segment chaining at all, and :data:`MAX_CLIP_SECONDS` is passed as an
explicit ``--time-limit`` so the bound is a number this repository owns, is
swept by ``tests/mobile/test_mobile_bounds_upper.py``, and is unreachable by
construction rather than by hope.

**One producer, one meaning, EVERY consumer.** Every outcome is one of
:data:`STATES`, and each state has exactly one sentence in :data:`NOTES`. The
report renders ``NOTES[state]`` and never invents its own wording, so a state
added here without a consumer is visible in the page instead of falling through
to a generic branch and telling a tester nothing.

**A credential is withheld, not masked.** ``run_store.redact`` masks strings; a
frame is pixels and there is nowhere in a PNG to put a mask. So a step whose
script carries a credential stores NO picture at all: the recorder is stopped
and the file deleted on the device without ever being pulled, no frame is taken,
and the record says ``credential_withheld`` in this module's own words. Both
value-bearing ops count -- see :func:`withholds`.

**The stop path costs WALL CLOCK, and its ceiling is named here.** Stopping a
recorder is an interrupt on the device followed by a wait, and both halves are
bounded by :data:`MAX_STOP_WAIT_S` -- deliberately NOT ``adb.DEFAULT_TIMEOUT_S``
(30 seconds). On any image where ``pkill`` is absent, or where the interrupt
never reaches the recorder, waiting an adb default would cost a tester THIRTY
SECONDS PER STEP for a picture. After the window the adb CLIENT is killed
rather than waited on further: the recording is already unusable at that point,
and the only remaining question is how long the run pays for it. The worst case
is therefore about two times :data:`MAX_STOP_WAIT_S` per step, and it is stated in
the plan's risk table as a number rather than as "bounded by adb's timeout".

**``pkill -INT screenrecord`` is device-GLOBAL, and that is not fixable here.**
There is no pid to target: the recorder is spawned by the shell adb runs, and
its pid is not ours. So this interrupts EVERY ``screenrecord`` on that serial.
What makes it tolerable is the per-device lock in ``tools/mobile/locks.py`` --
one run owns a serial at a time -- so the only other recording that can exist
is one a human started by hand on the same device.

Every public function returns ``{"error", "content"}`` and never raises: a
picture is not a verdict, and nothing here may cost a tester a step.
"""

from __future__ import annotations

import asyncio  # noqa: F401
import logging  # noqa: F401
import re  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any  # noqa: F401

from tools.mobile import actions as actions_mod  # noqa: F401
from tools.mobile import adb, paths, run_store  # noqa: F401
from tools.mobile.media_args import ClipRef, _PullJob, _Step  # noqa: F401
from tools.mobile.media_clip import start_clip  # noqa: F401
from tools.mobile.media_consts import (  # noqa: F401
    _UNSAFE,
    CLIP,
    FRAME,
    MAX_CLIP_BYTES,
    MAX_CLIP_SECONDS,
    MAX_CLIPS_PER_CASE,
    MAX_STOP_WAIT_S,
    MEDIA_DIRNAME,
    NOT_ATTEMPTED,
    NOTES,
    OK,
    PULL_FAILED,
    REFUSED,
    REMOTE_DIR,
    REPORT_DIRNAME,
    STATES,
    TRUNCATED,
    WITHHELD,
    _safe,
    media_dir,
    record,
)
from tools.mobile.media_defer import flush, open_clips  # noqa: F401
from tools.mobile.media_finish import (  # noqa: F401
    _finish_step_pull,
    finish_step,
)
from tools.mobile.media_stop import _stop_recorder, abandon  # noqa: F401
from tools.mobile.media_store import records_of, remember  # noqa: F401
from tools.mobile.media_withhold import _as_dict, withholds  # noqa: F401

logger = logging.getLogger(__name__)
