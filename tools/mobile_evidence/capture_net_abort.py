"""Abandoning a network capture: :func:`abort_network`.

Stop a capture whose case will never be replayed, and delete its file.
``start_case`` starts the capture BEFORE the launch and the foreground wait, and
either of those can refuse the case. Without this the console keeps recording every
packet on the device until the next case's start replaces it, the sampler task keeps
polling, and a packet dump sits in the AVD directory with nothing left to read it.
``{"error", "content": <record>}`` with the stage ``ABANDONED_STAGE``. Never raises.
"""

from __future__ import annotations

import logging
import os

from tools.mobile import adb
from tools.mobile_evidence import capture_consts as _consts
from tools.mobile_evidence.capture_console import _avd_path
from tools.mobile_evidence.capture_net_args import AbortReq
from tools.mobile_evidence.capture_pcap import _pcap_path, _read_and_delete
from tools.mobile_evidence.capture_records import _network_skipped, _not_started
from tools.mobile_evidence.capture_sampler import _stop_sampler

logger = logging.getLogger("tools.mobile_evidence.capture")


async def _abort_stop(serial: str, prior: dict) -> None:
    stopped = await adb.emu(serial, "network", "capture", "stop")
    if stopped.get("error"):
        logger.info(
            "mobile_evidence.capture: the abandoned capture did not stop cleanly: %s",
            str(stopped["error"])[:200],
        )
    located = await _avd_path(serial)
    target = (
        _pcap_path(located.get("content"), prior.get("file"))
        if not located.get("error")
        else None
    )
    if target is not None and os.path.exists(target):
        _read_and_delete(target)


async def _abort_guarded(req: AbortReq) -> dict:
    try:
        prior = req.begin_net if isinstance(req.begin_net, dict) else {}
        _stop_sampler((str(req.run_id), str(req.tc_id)))
        if not prior.get("started"):
            return _not_started(prior)
        await _abort_stop(req.serial, prior)
        return _network_skipped(_consts.ABANDONED_REASON, _consts.ABANDONED_STAGE)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile_evidence.capture.abort_network failed")
        return _network_skipped(str(exc)[:200], _consts.ABANDONED_STAGE)


async def abort_network(req: AbortReq) -> dict:
    """Stop a capture whose case will never be replayed, and delete its file."""
    return await _abort_guarded(req)
