"""Bookkeeping around one replayed mobile step: inputs, audit row, footer."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, cast

from handlers import _common
from handlers.mobile.step_state import _MOBILE_SHOT_WANT, _note_mobile_step_case
from tools.mobile import case_runner as _case_runner
from tools.mobile import dump_latency, run_store, session_audit, step_timing
from tools.mobile.session_audit import AuditFacts

logger = logging.getLogger("tools.mcp_handlers")


@dataclass(frozen=True)
class StepTurn:
    """What one replayed step needs: the run, its lease, the call and its budget."""

    run_id: str
    token: str
    call: dict
    budget: Any = None
    progress: _common.ProgressCb = None
    parsed_inputs: dict = field(default_factory=dict)


def _parse_tester_inputs(tester_inputs: str) -> dict:
    """The tester's per-field values as a dict; anything unparseable is ``{}``."""
    if not tester_inputs:
        return {}
    try:
        loaded = json.loads(tester_inputs)
    except (ValueError, TypeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _flag_visual_check(case: dict) -> None:
    """Ask the next packet to carry the picture when a ``visual`` assert stopped the replay."""
    # A `visual` assert stopped this replay (S1): the model asked to judge THIS
    # screen, so the next packet carries its picture. The LAST entry only -- the
    # trace is merged across submits.
    _trace = case.get("trace") if isinstance(case.get("trace"), list) else []
    _want = _MOBILE_SHOT_WANT.get()
    if (
        _trace
        and isinstance(_trace[-1], dict)
        and _trace[-1].get("outcome") == "visual_check"
        and isinstance(_want, dict)
    ):
        _want["visual"] = True


# Forget the run's briefing when the replay needed the model.
#
# A script this server could not parse is the observable signal that the
# model no longer has the static block in front of it -- a compacted chat, or
# one that never received it. Forgetting the briefing is what makes the
# omission self-healing: the very next packet carries the whole block again.
# Constraint 5 of the contract.
def _forget_briefing_if_needs_model(run_id: str, case: dict) -> None:
    """Forget the run's briefing when the replay needed the model."""

    if str(case.get("status") or "") != _case_runner.NEEDS_MODEL:
        return
    run_store.clear_briefing(run_id)
    run_store.clear_screen(run_id)
    _flag_visual_check(case)


def _audit_row_detail(turn: StepTurn, case: dict) -> dict:
    """The audit row's detail for one step, built from the turn and its case."""

    call = turn.call
    return session_audit.audit_detail(
        AuditFacts(
            run_id=turn.run_id,
            tc_id=str(case.get("tc_id") or call["tc_id"]),
            verdict=str(case.get("verdict") or ""),
            status=str(case.get("status") or ""),
            field=str(call["tester_input_field"] or ""),
            fields=list(turn.parsed_inputs.keys()) or None,
        )
    )


# A script this server could not parse is the observable signal that the
# model no longer has the static block in front of it -- a compacted
# chat, or one that never received it. Forgetting the briefing here is
# what makes the omission self-healing: the very next packet carries the
# whole block again, at the cost of the one turn that already went wrong
# rather than of the run. Constraint 5 of the contract.
async def _mobile_step_audit(turn: StepTurn, case: dict) -> None:
    """Note the case, write the audit row, forget the briefing if needed."""
    _note_mobile_step_case(case)
    await _common.audit(
        "mcp_mobile_step",
        entity_id=turn.run_id,
        detail=_audit_row_detail(turn, case),
    )
    _forget_briefing_if_needs_model(turn.run_id, case)


def _run_serial(body: dict) -> str:
    """The serial of the device this run resolved, ``""`` if none."""
    _res: dict = (
        cast("dict", body.get("resolved"))
        if isinstance(body.get("resolved"), dict)
        else {}
    )
    return str(_res.get("serial") or "")


def _slow_notes(run_id: str, serial: str) -> str:
    """This device's dump-latency notes, each on its own line."""

    # This device's dump latency and -- once per run -- the warning that
    # the host or the AVD is the bottleneck (fix round 3, item 2). The
    # serial is the run body's own, the one `_mobile_packet_text` reads.
    slow: list = []
    if serial:
        slow = [
            part
            for part in (
                dump_latency.describe(serial),
                dump_latency.host_warning_once(run_id, serial),
            )
            if part
        ]
    return "".join("\n" + part for part in slow)


def _mobile_step_footer(run_id: str, tc_id: str, body: dict) -> str:
    """The timing line plus this device's dump-latency notes, ready to append."""

    # LAST, so the next packet's screenshot is inside the total. The same
    # line goes to the log, where the audit row's one duration_ms cannot
    # say which phase spent it.
    timing = step_timing.line()
    logger.info("mobile step %s %s: %s", run_id, tc_id, timing)
    run_store.append_step_timing(run_id, step_timing.current_row(0))
    return "\n\n" + timing + _slow_notes(run_id, _run_serial(body))
