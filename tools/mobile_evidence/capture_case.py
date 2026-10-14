"""The per-case calls of the app-log lane: :func:`begin_case` and :func:`slice_case`.

* :func:`begin_case` -- ``logcat -c`` so the ring buffer starts with the app's own
  start-up, the device clock (for the offset the join needs, plan D6) and the app's
  pid (so the slice can be narrowed to it). ``{"error", "content": {"skipped",
  "profile", "clock_offset_ms", "pid", "clock_note"}}``. ``clock_offset_ms`` is
  device minus host, in ms; ``None`` when the device clock could not be read, in
  which case ``clock_note`` carries ``CLOCK_NOT_READ`` -- never a silent zero.
  Never raises.
* :func:`slice_case` -- ``logcat -d`` at every checkpoint, filtered to the profile's
  tag and the pid, SCRUBBED line by line, then written under
  ``evidence/<tc_id>/logcat-<index>.txt``. ``{"error", "content": {"skipped",
  "path", "lines", "truncated", "crash"}}``. ``crash`` is ``crash_detector.scan``'s
  record for THIS slice -- computed here because this is the one moment the slice
  text exists in memory, and computed AFTER the scrub so a tester's typed value
  cannot reach a report or a chat inside a crash excerpt. ``None`` means the slice
  was not judged (skipped, or the dump failed), which is not the same as no crash.
  Never raises.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple, cast

from tools.mobile_evidence import capture_consts as _consts
from tools.mobile_evidence import capture_gate, crash_detector
from tools.mobile_evidence.capture_logdev import (
    _app_pid,
    _clear_logcat,
    _clock_offset,
    _dump_slice,
    _slice_pid,
)
from tools.mobile_evidence.capture_profile import (
    _is_generic,
    _profile_label,
    _profile_or_generic,
)
from tools.mobile_evidence.capture_records import _Judged, _slice_record, _slice_skipped
from tools.mobile_evidence.capture_refs import CaseRef, SliceCtx
from tools.mobile_evidence.capture_scrub import _SliceDest, _write_slice

logger = logging.getLogger("tools.mobile_evidence.capture")


class _SliceReq(NamedTuple):
    serial: str
    package: str
    run_id: str
    tc_id: str
    case_index: int
    tester_inputs: dict | None


async def _begin_content(serial: str, package: str, tc_id: str) -> dict:
    # No profile is no longer a reason to capture nothing: the app still has
    # a pid, and its logcat filtered to that pid is its own output.
    profile = _profile_or_generic(package)
    await _clear_logcat(serial, tc_id)
    offset = await _clock_offset(serial)
    pid = await _app_pid(serial, package)
    return {
        "error": None,
        "content": {
            "skipped": None,
            "profile": _profile_label(profile),
            "clock_offset_ms": offset,
            "pid": pid,
            "clock_note": None if offset is not None else _consts.CLOCK_NOT_READ,
        },
    }


async def begin_case(case: CaseRef) -> dict:
    """Clear the ring buffer and read the device clock and the app's pid."""
    try:
        refusal = capture_gate.gate_refusal()
        if refusal:
            return {"error": refusal, "content": None}
        return await _begin_content(case.serial, case.package, case.tc_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile_evidence.capture.begin_case failed")
        return {"error": str(exc), "content": None}


def _slice_judge(req: _SliceReq, body: dict) -> dict:
    name = "logcat-" + str(max(0, int(cast("Any", req.case_index) or 0))) + ".txt"
    dest = _SliceDest(req.run_id, req.tc_id, name)
    written, text, count = _write_slice(body, dest, req.tester_inputs)
    if written.get("error"):
        return written
    # The slice is judged HERE, on the scrubbed text, once. Reading
    # it back off disk to judge it would be a second read of the
    # same bytes and a second place that knows the file layout.
    crash = crash_detector.scan(text, req.package)
    return _slice_record(written, body, _Judged(count, crash))


async def _slice_body(req: _SliceReq, began: dict) -> dict:
    profile = _profile_or_generic(req.package)
    pid = await _slice_pid(req.serial, req.package, began)
    if _is_generic(profile) and pid is None:
        return _slice_skipped(_consts._UNSCOPED_GENERIC_REASON)
    dumped = await _dump_slice(req.serial, profile, pid)
    if dumped.get("error"):
        return dumped
    return _slice_judge(req, dumped.get("content") or {})


async def _slice_guarded(req: _SliceReq, begin: dict | None) -> dict:
    try:
        refusal = capture_gate.gate_refusal()
        if refusal:
            return {"error": refusal, "content": None}
        began = begin if isinstance(begin, dict) else {}
        if began.get("skipped"):
            return _slice_skipped(str(began["skipped"])[:200])
        return await _slice_body(req, began)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile_evidence.capture.slice_case failed")
        return {"error": str(exc), "content": None}


async def slice_case(case: CaseRef, index: int, ctx: SliceCtx) -> dict:
    """``logcat -d`` for this case, scrubbed, written as ``evidence/<tc_id>/logcat-<index>.txt``."""
    req = _SliceReq(
        case.serial, case.package, case.run_id, case.tc_id, index, ctx.tester_inputs
    )
    return await _slice_guarded(req, ctx.begin)
