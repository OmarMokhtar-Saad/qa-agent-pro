"""API capture for the mobile lane -- paths, trust ledger and CA (Phase 1).

This package is a SIBLING of ``tools/mobile`` rather than ``tools/mobile/capture``
on purpose: ``tools/mobile_evidence/capture.py`` already exists and is imported
under the bare name ``capture`` in ``tools/mobile/case_runner.py`` and
``tools/mobile/session_explore.py``. A second module reachable as ``capture`` in the
same call graph is one name with two meanings, which this project has shipped
before. Consumers import this one as ``from tools import mobile_capture as
api_capture``.

Every public function in this package returns ``{"error", "content"}`` and never
raises. Capture is EVIDENCE: an evidence fault may no more change a verdict than
a failed log slice can.

Phase 1 is deliberately device-free and network-free. Nothing here spawns a
process, opens a socket or calls adb; the proxy, the probe and the cert install
arrive in later phases.

The split of ownership this package assumes, stated once so a reader of one file
does not have to infer it: device trust is per SERIAL and outlives every run
(``ledger.py``); what was OBSERVED is per CASE and belongs to the run (later
phases, written under ``runs/``); a live proxy is neither -- it is a runtime
marker whose only job is to make the next run's reap possible after a crash.
"""

from __future__ import annotations

import logging

from tools.mobile import device_lock
from tools.mobile_capture import ladder
from tools.mobile_capture.prepare_flow import (  # noqa: F401  re-exports: old home of the helpers
    _Attempt,
    _ca_fingerprint_and_prior,
    _decide_reply,
    _mitmdump_binary,
    _none_reply,
    _prepare_locked,
    _start_and_probe,
)

logger = logging.getLogger(__name__)


# The ONE orchestrator both call sites reuse (Phase 6, T3.2a/T4.2a): the
# consent stage inside ``handle_mobile_test`` and the standalone
# ``qa_setup_capture`` tool. Neither ever re-decides this on its own --
# they call this function and fold its reply into their own reply.
#
# ``owner`` is the caller's OWN device-lock label when it has one (the
# run's ``pre_run_owner``/``run_id``, per ``device_lock.take_device_lock``'s
# reentrant-same-owner contract) -- passing it makes every lock take below
# a free re-entry. With no ``owner`` (the standalone tool, which has no
# run), this mints a throwaway provisioning label and releases it in a
# ``finally``.
#
# Never raises; always ``{"error": None, "content": <ladder.record(...)>}``.
# An evidence/provisioning fault is reported as a refusal-by-name tier, not
# an exception a caller has to separately handle.
async def prepare(serial: str, *, owner: str = "", apply_: bool = False) -> dict:
    """The ONE orchestrator both call sites reuse (Phase 6, T3.2a/T4.2a)."""

    serial = str(serial or "")
    minted = not str(owner or "").strip()
    label = str(owner or "").strip() or device_lock.new_provisioning_owner()

    try:
        return await _prepare_locked(serial, label, apply_)
    except Exception:
        logger.exception("mobile_capture.prepare failed")
        return _none_reply(_Attempt(serial, label), ladder.REASON_DEVICE_GONE)
    finally:
        if minted:
            device_lock.release_device_lock(
                label, device_lock.DeviceRelease(as_holder=True)
            )
