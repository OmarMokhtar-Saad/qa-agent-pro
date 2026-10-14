"""Mobile session explore lane: per-turn network capture (split from session_explore)."""

from __future__ import annotations

import logging

from tools.mobile import case_runner
from tools.mobile.replay.context import Context
from tools.mobile.session_explore_ids import explore_turn_tc_id
from tools.mobile_evidence import capture

logger = logging.getLogger("tools.mobile.session")


async def _abort_prior(run_id: str, ctx: Context, body: dict) -> None:
    """Abort a capture the state still records as started."""
    # A RE-ISSUED packet for the same turn (a refused script, a resumed
    # chat) must not leave the first capture running: the console records
    # until it is told to stop, and the second start would write the same
    # file name over a capture nothing had read.
    prior = body.get("network")
    if isinstance(prior, dict) and prior.get("started"):
        tc_id = explore_turn_tc_id(body.get("turn"))
        await capture.abort_network(capture.AbortReq(ctx.serial, run_id, tc_id, prior))


# Returns the state to persist. Never raises: a capture is evidence, and
# evidence may not cost a turn.
async def _explore_begin_network(run_id: str, state: object, ctx: Context) -> dict:
    """Start this turn's capture and put its record in the explore state."""
    body = dict(state if isinstance(state, dict) else {})
    try:
        tc_id = explore_turn_tc_id(body.get("turn"))
        await _abort_prior(run_id, ctx, body)
        body["network"] = case_runner.network_record(
            await capture.begin_network(
                capture.BeginReq(ctx.serial, ctx.package, run_id, tc_id)
            )
        )
    except Exception:  # never-raise: evidence is not a turn
        logger.exception("mobile.session._explore_begin_network failed")
    return body


# Returns the state to persist. The record is REMOVED rather than kept: the
# file is gone, so a record still saying ``started`` would have the next
# begin abort something that no longer exists. Never raises.
async def _explore_abort_network(run_id: str, state: object, ctx: Context) -> dict:
    """Stop a capture whose turn will never be replayed, and drop its record."""
    body = dict(state if isinstance(state, dict) else {})
    prior = body.get("network")
    if not (isinstance(prior, dict) and prior.get("started")):
        return body
    try:
        tc_id = explore_turn_tc_id(body.get("turn"))
        await capture.abort_network(capture.AbortReq(ctx.serial, run_id, tc_id, prior))
    except Exception:  # never-raise: evidence is not a turn
        logger.exception("mobile.session._explore_abort_network failed")
    body.pop("network", None)
    return body


def _finish_failed(exc: Exception) -> dict:
    """The record a failed stop reports: the failure names itself."""
    # A BLANK record here is what made the report say the capture ran and
    # found nothing. The failure names itself instead, so the page shows a
    # reason rather than a false negative.
    return case_runner.network_record(
        {
            "stage": "finish",
            "skipped": (
                "the capture could not be stopped and read for this turn: "
                + str(exc)[:200]
            ),
        }
    )


# The record is READ from the state the packet build persisted, so a turn
# submitted from another chat still finds its own capture.
async def _explore_finish_network(run_id: str, state: object, ctx: Context) -> dict:
    """Stop this turn's capture and return its record for the checkpoint."""
    body = state if isinstance(state, dict) else {}
    try:
        tc_id = explore_turn_tc_id(body.get("turn"))
        req = capture.FinishReq(
            ctx.serial, ctx.package, run_id, tc_id, 0, body.get("network")
        )
        return case_runner.network_record(await capture.finish_network(req))
    except Exception as exc:  # never-raise: evidence is not a verdict
        logger.exception("mobile.session._explore_finish_network failed")
        return _finish_failed(exc)
