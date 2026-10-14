"""The case's evidence lanes: opening them, abandoning them, splicing them in."""

from __future__ import annotations

import logging

from tools.mobile import run_store
from tools.mobile.case_run_record import (
    _evidence_record,
    capture_record,
    network_record,
)
from tools.mobile.case_run_types import _Ref
from tools.mobile.replay.context import Context
from tools.mobile_capture import flows as api_flows
from tools.mobile_evidence import capture

# Same object as case_run_consts.logger (getLogger is a registry); a local
# binding lets ruff see the handler below as logged (BLE001).
logger = logging.getLogger("tools.mobile.case_runner")


async def _begin_evidence(run_id: str, tc_id: str, ctx: Context) -> dict:
    """Open the per-case evidence lanes, between the force-stop and the launch.

    None of them can block the case or change a verdict: a lane that is off,
    refused or unavailable lands as a record which SAYS so, never a blank.
    """
    # App evidence (plan D5): the ring buffer is cleared BETWEEN the stop
    # and the launch, so a slice begins with the app's own start-up.
    case = capture.CaseRef(ctx.serial, ctx.package, run_id, tc_id)
    evidence = _evidence_record(await capture.begin_case(case))
    # The wire, beside the log (plan mobile-network-capture), started HERE so
    # the capture covers the app's own start-up.
    req = capture.BeginReq(ctx.serial, ctx.package, run_id, tc_id)
    evidence["network"] = network_record(await capture.begin_network(req))
    # The API-capture lane's own per-case boundary (Phase 6, T6.2): a second,
    # independent dimension on the same seam.
    evidence["capture"] = capture_record(api_flows.mark_case_current(run_id, tc_id))
    return evidence


# Stop the case's network capture and hand *result* back UNCHANGED.
#
# The early returns of ``start_case`` after the capture has started all
# pass through here, so a refused case cannot leave the console recording and
# a packet dump on disk. The abort can never change what the caller is told:
# evidence is not a verdict, and a failure to stop is logged, not raised.
async def _abandon(ref: _Ref, evidence: dict, result: dict) -> dict:
    """Stop the case's network capture and hand *result* back UNCHANGED."""
    try:
        await capture.abort_network(
            capture.AbortReq(
                ref.ctx.serial,
                ref.run_id,
                ref.tc_id,
                (evidence or {}).get("network"),
            )
        )
        # Stops the API-capture lane the same way: an abandoned case must
        # not go on attributing flows to a case that never finished.
        api_flows.mark_case_finished(ref.run_id)
    except Exception:  # never-raise: evidence is not a verdict
        logger.exception("mobile.case_runner._abandon failed")
    return result


def _finish_req(evidence: dict, ref: _Ref) -> capture.FinishReq:
    """The network finish request for the case's last slice."""
    last = max(0, evidence["slices"] - 1)
    ctx = ref.ctx
    return capture.FinishReq(
        ctx.serial, ctx.package, ref.run_id, ref.tc_id, last, evidence.get("network")
    )


# The wire is stopped, parsed and attributed at the SAME checkpoint the
# log slice is taken, and spliced in beside it. Nothing branches on the
# result: an evidence fault can no more change a verdict here than a
# failed slice can, which is why this runs after the crash merge and
# before the write rather than anywhere a return could skip it.
#
# The API-capture lane (second statement): same checkpoint, same discipline,
# its own dimension: never branched on, an evidence fault here can no more
# change a verdict than a failed log slice can.
async def _finish_wire(evidence: dict, ref: _Ref) -> None:
    """Stop the network capture and the API-capture lane into the record."""
    ctx = ref.ctx
    evidence["network"] = network_record(
        await capture.finish_network(_finish_req(evidence, ref))
    )
    evidence["capture"] = capture_record(
        api_flows.finish_case(ref.run_id, ref.tc_id, tester_inputs=ctx.tester_inputs)
    )


def _prior_content(run_id: str, tc_id: str) -> dict:
    """The case's stored body, or ``{}`` when there is none or it is not a dict."""
    prior = (run_store.read_case(run_id, tc_id) or {}).get("content")
    return prior if isinstance(prior, dict) else {}


# NEVER write a document read before an await. A checkpoint may have
# landed while the device was being read; the fresh copy carries its
# verdict and trace, and only the evidence record is spliced in.
def _splice_evidence(ref: _Ref, evidence: dict, prior: dict) -> None:
    """Write the evidence record onto a FRESH read of the case."""
    fresh = (run_store.read_case(ref.run_id, ref.tc_id) or {}).get("content")
    fresh = fresh if isinstance(fresh, dict) else prior
    fresh["evidence"] = evidence
    run_store.write_case(ref.run_id, ref.tc_id, fresh)
