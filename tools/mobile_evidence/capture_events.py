"""The run-end pull: :func:`pull_events` writes ``evidence/events.ndjson``.

``run-as <pkg> cat`` of every segment of the app's own event log, SCRUBBED record
by record, concatenated into ``evidence/events.ndjson``. The typed-value net is
armed with *tester_inputs* plus every value ``remember_typed`` saw for this run,
for the duration of the record loop (plan D4) -- the app echoes what was typed
into its own log.

``{"error", "content": {"events_source": "ndjson"|"logcat-only", "path",
"reason", "segments", "lines", "truncated"}}``. A ``run-as`` refusal (release
build, unknown package) is the logcat-only branch WITH its reason, and nothing
raises. A package with no profile makes NO adb call for events: the result says
``events_source: "logcat-only"`` with the reason.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from tools.mobile import run_store
from tools.mobile_evidence import capture_consts as _consts
from tools.mobile_evidence import capture_gate
from tools.mobile_evidence.capture_logdev import (
    _LogTarget,
    _read_segments,
    _segment_names,
)
from tools.mobile_evidence.capture_profile import (
    _no_log_dir_reason,
    _profile_or_generic,
)
from tools.mobile_evidence.capture_records import (
    _events_record,
    _EventStats,
    _logcat_only,
)
from tools.mobile_evidence.capture_refs import RunRef
from tools.mobile_evidence.capture_typed import _TYPED_BY_RUN, forget_run
from tools.mobile_shared import scrub

logger = logging.getLogger("tools.mobile_evidence.capture")


class _PullReq(NamedTuple):
    serial: str
    package: str
    run_id: str
    tester_inputs: dict | None


async def _pull_read(
    req: _PullReq, target: _LogTarget, names: list[str]
) -> tuple[dict | None, list[str], bool]:
    typed = set(_TYPED_BY_RUN.get(str(req.run_id), set()))
    typed.update(str(v) for v in (req.tester_inputs or {}).values())
    scrub.arm(typed)
    try:
        return await _read_segments(target, names)
    finally:
        scrub.forget_sensitive()


async def _pull_body(req: _PullReq) -> dict:
    profile = _profile_or_generic(req.package)
    log_dir = profile.log_dir()
    if not log_dir:
        return _logcat_only(_no_log_dir_reason(profile, req.package))
    target = _LogTarget(req.serial, req.package, log_dir)
    names, refused = await _segment_names(target, profile)
    if refused is not None:
        return refused
    refused, parts, truncated = await _pull_read(req, target, names)
    if refused is not None:
        return refused
    text = ("\n".join(parts) + "\n") if parts else ""
    written = run_store.write_evidence_text(
        run_store.EvidenceRef(req.run_id, None, _consts.EVENTS_FILE), text
    )
    if written.get("error"):
        return written
    return _events_record(written, _EventStats(len(names), len(parts), truncated))


async def _pull_guarded(req: _PullReq) -> dict:
    # The run's typed values are RELEASED ON EVERY EXIT, not only after a
    # successful pull: a release build refuses run-as and an unprofiled
    # package never gets this far, and both are routine -- a typed credential
    # must not outlive the run in process memory on either path.
    try:
        refusal = capture_gate.gate_refusal()
        if refusal:
            return {"error": refusal, "content": None}
        return await _pull_body(req)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile_evidence.capture.pull_events failed")
        return {"error": str(exc), "content": None}
    finally:
        forget_run(req.run_id)


async def pull_events(run: RunRef, tester_inputs: dict | None = None) -> dict:
    """Pull the app's own event log segments into ``evidence/events.ndjson``."""
    req = _PullReq(run.serial, run.package, run.run_id, tester_inputs)
    return await _pull_guarded(req)
