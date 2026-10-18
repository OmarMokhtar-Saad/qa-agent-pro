"""Constants, states and the record shape of the media lane."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tools.mobile import paths

#: The report is a self-contained FOLDER: its index and its assets together.
REPORT_DIRNAME = "report"
MEDIA_DIRNAME = "media"

#: Seconds of one clip, passed to ``screenrecord --time-limit``. One replay is
#: bounded by ``actions.SUBMIT_BUDGET_MS`` (40s), so this sits above what a step
#: can produce and far below what the device would impose.
MAX_CLIP_SECONDS = 60

#: Bytes of one clip that is KEPT. A larger file is deleted rather than stored,
#: because a run folder a tester cannot mail is not evidence.
MAX_CLIP_BYTES = 24 * 1024 * 1024

#: Clips stored for one case. ``actions.MAX_ACTIONS`` bounds one script, and a
#: case cannot submit more steps than the run's own step budget allows.
MAX_CLIPS_PER_CASE = 40

#: Seconds spent STOPPING one recorder: the interrupt call and the wait for the
#: recorder to close its container are each bounded by this, and the adb client
#: is killed once it expires. NOT ``adb.DEFAULT_TIMEOUT_S`` -- a 30-second wait
#: per step is a tester's time spent on a picture, and it is the difference
#: between a bounded cost and an unstated one.
#:
#: The ``MAX_`` prefix is load-bearing, not decoration: ``tests/cap_scanner``
#: decides what IS a cap by splitting the name on ``_`` and intersecting the
#: segments with ``CAP_WORDS``. ``STOP_WAIT_S`` -- the name this constant nearly
#: shipped under -- has no such segment, so the completeness sweep in
#: ``test_mobile_bounds_upper.py`` would never have forced a CEILINGS row for
#: it, and the one cap here that is paid on EVERY step would have been the one
#: cap with no ceiling.
MAX_STOP_WAIT_S = 4

OK = "ok"
TRUNCATED = "truncated"
REFUSED = "refused"
PULL_FAILED = "pull_failed"
WITHHELD = "credential_withheld"
NOT_ATTEMPTED = "not_attempted"

#: One sentence per state, and the ONLY wording any consumer may show. Each is
#: a different fact about the run and only some are worth investigating.
NOTES: dict = {
    OK: "This is the device's own screen, recorded while the step replayed.",
    TRUNCATED: (
        "The recording reached its "
        + str(MAX_CLIP_SECONDS)
        + "-second limit and stops before the step did; what is here is real, "
        "and the end of the step is missing."
    ),
    REFUSED: (
        "No recording: the device refused to record this step (an emulator "
        "without the recorder, a secure window, or a screenrecord that would "
        "not start). The step itself ran normally."
    ),
    PULL_FAILED: (
        "The step WAS recorded on the device, and the file could not be copied "
        "back to this machine. Nothing about the step's verdict depends on it."
    ),
    WITHHELD: (
        "WITHHELD (credential entered): no picture of this step is stored, "
        "deliberately: a credential was "
        "entered while it ran, and a frame cannot be masked the way text can."
    ),
    NOT_ATTEMPTED: (
        "No picture was attempted for this step. Nothing failed on the device."
    ),
}

#: The states, in the order a reader meets them. A consumer enumerates THIS.
STATES: tuple = tuple(NOTES)

CLIP = "clip"
FRAME = "frame"

#: Where a recording lives while it is being made. The device path charset is
#: re-checked by ``adb`` itself; this is the directory every Android build has.
REMOTE_DIR = "/sdcard"

#: The charset a stored file name may use. ``report._MEDIA_NAME`` is the
#: CONSUMER side of this one rule, and the two are pinned against each other in
#: ``tests/mobile/test_mobile_report_media.py``.
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def media_dir(run_id: object) -> Path:
    """``runs/<run_id>/report/media``. Pure path arithmetic; creates nothing."""
    return paths.run_dir(str(run_id)) / REPORT_DIRNAME / MEDIA_DIRNAME


def _safe(value: object, limit: int = 40) -> str:
    return _UNSAFE.sub("-", str(value or ""))[:limit]


def record(kind: str, state: str, **extra: Any) -> dict:
    """One media record, in the shape the case checkpoint and report share."""
    body = {"kind": str(kind), "state": str(state), "file": ""}
    body.update({str(k): v for k, v in extra.items()})
    return body
