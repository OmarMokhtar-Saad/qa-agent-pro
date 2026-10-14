"""Finishing the network capture: :func:`finish_network`.

Stop the capture, parse it, attribute it, write the summary, delete the pcap.
``{"error", "content": <network record>}``. Every failure names the STAGE it
happened at (``device``, ``start``, ``locate``, ``read``, ``parse``, ``finish``),
because "no network section" with no reason is what makes a tester re-run a case
that will fail the same way. Never raises.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from tools.mobile import adb
from tools.mobile_evidence import capture_gate, netattr, pcap
from tools.mobile_evidence.capture_console import _avd_path
from tools.mobile_evidence.capture_net_args import FinishReq
from tools.mobile_evidence.capture_pcap import _read_pcap
from tools.mobile_evidence.capture_records import _network_skipped, _not_started
from tools.mobile_evidence.capture_rows import (
    _attributed_rows,
    _parsed_record,
    _ParsedParts,
    _SummaryDest,
    _write_network_summary,
)
from tools.mobile_evidence.capture_sampler import _stop_sampler

logger = logging.getLogger("tools.mobile_evidence.capture")


class _Captured(NamedTuple):
    prior: dict
    state: dict
    notes: list[str]
    data: bytes


def _finish_parse(req: FinishReq, got: _Captured) -> dict:
    parsed = pcap.parse(got.data)
    if parsed.get("error"):
        return _network_skipped(str(parsed["error"]), "parse")
    body = parsed.get("content") or {}
    if body.get("note"):
        got.notes.append(str(body["note"]))
    rows, sampled = _attributed_rows(body, got.state, got.prior)
    if sampled:
        got.notes.append(netattr.SOCKET_NOTE)
    parts = _ParsedParts(got.prior, body, got.state, rows, got.notes)
    record = _parsed_record(parts)
    _write_network_summary(_SummaryDest(req.run_id, req.tc_id, req.case_index), record)
    return {"error": None, "content": record}


async def _finish_capture(req: FinishReq, prior: dict, state: dict) -> dict:
    stopped = await adb.emu(req.serial, "network", "capture", "stop")
    notes = [str(prior.get("note") or "")]
    if stopped.get("error"):
        notes.append("the capture did not stop cleanly: " + str(stopped["error"]))
    located = await _avd_path(req.serial)
    data, failed = _read_pcap(located, prior.get("file"))
    if failed is not None:
        return failed
    return _finish_parse(req, _Captured(prior, state, notes, data))


async def _finish_guarded(req: FinishReq) -> dict:
    try:
        refusal = capture_gate.gate_refusal()
        if refusal:
            return {"error": refusal, "content": None}
        prior = req.begin_net if isinstance(req.begin_net, dict) else {}
        state = _stop_sampler((str(req.run_id), str(req.tc_id)))
        if not prior.get("started"):
            return _not_started(prior)
        return await _finish_capture(req, prior, state)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile_evidence.capture.finish_network failed")
        return _network_skipped(str(exc)[:200], "finish")


async def finish_network(req: FinishReq) -> dict:
    """Stop the capture, parse it, attribute it, write the summary, delete the pcap."""
    return await _finish_guarded(req)
