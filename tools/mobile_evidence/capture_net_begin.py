"""Starting the network capture: :func:`begin_network`.

Start the console capture and the socket sampler for one case.
``{"error", "content": <network record>}``. ``error`` only for a flag refusal, so
the caller can tell "the tester turned this off" from "this run is on a phone".
Never raises, and no failure here can block a case.
"""

from __future__ import annotations

import logging
import time

from tools.mobile import adb
from tools.mobile_evidence import capture_gate, netattr
from tools.mobile_evidence.capture_console import (
    _app_uid,
    _capture_name,
    _capture_start,
)
from tools.mobile_evidence.capture_net_args import BeginReq
from tools.mobile_evidence.capture_records import _network_record, _network_skipped
from tools.mobile_evidence.capture_sampler import _start_sampler

logger = logging.getLogger("tools.mobile_evidence.capture")


def _start_refusal(started: dict) -> dict:
    # WHICH refusal this was, decided by the NAMED constant rather than
    # by looking for words in the message: "this is a phone" and "the
    # console said no" are different news for a tester, and a substring
    # test would relabel one as the other the first time the wording
    # changed. `adb.emu` is the single place the emulator question is
    # asked, so this reads its answer instead of asking again.
    reason = str(started["error"])
    stage = "device" if reason == adb.NOT_AN_EMULATOR else "start"
    return _network_skipped(reason, stage)


async def _begin_started(req: BeginReq, name: str) -> dict:
    uid = await _app_uid(req.serial, req.package)
    # The uid is handed to the sampler, not looked up again inside it: the
    # endpoint map is built for THIS app and a second lookup would be a
    # second answer to the same question.
    _start_sampler(req.serial, (req.run_id, req.tc_id), uid)
    return {
        "error": None,
        "content": _network_record(
            stage="started",
            started=True,
            file=name,
            uid=uid,
            note="" if uid is not None else netattr.NO_UID_NOTE,
            started_ms=int(time.time() * 1000),
        ),
    }


async def _begin_guarded(req: BeginReq) -> dict:
    try:
        refusal = capture_gate.gate_refusal()
        if refusal:
            return {"error": refusal, "content": None}
        name = _capture_name(req.run_id, req.tc_id, req.case_index)
        started = await _capture_start(req.serial, name)
        if started.get("error"):
            return _start_refusal(started)
        return await _begin_started(req, name)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile_evidence.capture.begin_network failed")
        return _network_skipped(str(exc)[:200], "start")


async def begin_network(req: BeginReq) -> dict:
    """Start the console capture and the socket sampler for one case."""
    return await _begin_guarded(req)
