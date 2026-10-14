"""Case and escape packets: the planning packet for one test case."""

from __future__ import annotations

from typing import Any

from tools.mobile import actions as actions_mod
from tools.mobile import perception
from tools.mobile.run_packets_args import CaseRef
from tools.mobile.run_packets_base import (
    _PACKET_ERRORS,
    _observation_key,
    _packet_base,
    _screen_block,
    logger,
)
from tools.mobile.run_packets_text import (
    _BUDGET_NOTE,
    _CASE_INSTRUCTION,
    _ESCAPE_INSTRUCTION,
    _LAST_ESCAPE_NOTE,
    _RECOVERY_NOTE,
)
from tools.untrusted import wrap_untrusted

MAX_TRACE_ENTRIES = 40
MAX_CASE_STEPS = 40


def _case_head(body: dict) -> list[str]:
    lines = [
        str(body.get("tc_id") or "") + " " + str(body.get("title") or ""),
        "priority "
        + str(body.get("priority") or "?")
        + " | type "
        + str(body.get("type") or "?")
        + " | module "
        + str(body.get("module") or "?"),
    ]
    preconditions = str(body.get("preconditions") or "")
    if preconditions:
        lines.append("preconditions: " + preconditions)
    return lines


def _case_step_lines(body: dict) -> list[str]:
    lines: list[str] = []
    for step in (body.get("steps") or [])[:MAX_CASE_STEPS]:
        if not isinstance(step, dict):
            continue
        lines.append(
            str(step.get("step_number") or "?")
            + ". "
            + str(step.get("action") or "")
            + (
                " [data: " + str(step.get("test_data")) + "]"
                if str(step.get("test_data") or "")
                else ""
            )
            + " -> expected: "
            + str(step.get("expected_result") or "")
        )
    return lines


# The case as compact, wrapped text.
#
# Wrapped because a case can have come from a Jira ticket: its title and steps
# are as attacker-influenceable as the ticket text, and this block is going
# straight into a prompt.
def _case_block(view: object) -> str:
    body = view if isinstance(view, dict) else {}
    lines = _case_head(body) + _case_step_lines(body)
    return wrap_untrusted("test_case", "\n".join(lines), limit=8000)


def _turn_fields(view: object, screen: object, run_id: str) -> dict:
    return {
        "observation_id": _observation_key(screen),
        # ROUTING, like observation_id: `_mobile_packet_text` pops it and
        # attaches the screenshot only when it names a reason.
        "screen_unusable": perception.dump_unusable(screen),
        "case_block": _case_block(view),
        "screen_block": _screen_block(screen, run_id),
    }


def _escape_counts(escapes: int) -> dict:
    return {
        "escapes_used": int(escapes or 0),
        "escapes_left": max(0, 3 - int(escapes or 0)),
    }


def _case_worker_instructions() -> str:
    return (
        "One JSON object, key `actions`. Every action's `op` must be "
        "one of the listed ops. Stop the script where the screen "
        "stops telling you what happens next. " + _BUDGET_NOTE
    )


def build_case_job(view: object, screen: object, ref: CaseRef) -> dict:
    """The first planning packet for one case. Never raises; ``{}`` on failure."""
    try:
        packet = _packet_base(ref.run_id, ref.tc_id)
        packet.update(
            {
                "kind": "case",
                **_turn_fields(view, screen, ref.run_id),
                "instruction": _CASE_INSTRUCTION,
                **_escape_counts(ref.escapes),
                "worker_instructions": _case_worker_instructions(),
            }
        )
        return packet
    except _PACKET_ERRORS:  # pragma: no cover - defensive
        logger.warning("build_case_job failed", exc_info=True)
        return {}


def _trace_block(trace: Any) -> list[dict]:
    """The trace, already redacted by ``executor``, trimmed for a packet."""
    out: list[dict] = []
    for entry in list(trace or [])[:MAX_TRACE_ENTRIES]:
        if not isinstance(entry, dict):
            continue
        out.append(
            {
                "index": entry.get("index"),
                "action": actions_mod.redact_action(entry.get("action")),
                "outcome": str(entry.get("outcome") or ""),
                "detail": str(entry.get("detail") or "")[:300],
            }
        )
    return out


def _escape_worker_instructions(escapes: int) -> str:
    """The worker text of an escape packet; the last escape gets its extra note."""
    last = max(0, 3 - int(escapes or 0)) <= 1
    return (
        "One JSON object, key `actions`. If this case cannot be "
        "completed from here, say so with done(verdict='blocked', "
        "reason=...) rather than retrying the same action. "
        + _RECOVERY_NOTE
        + (_LAST_ESCAPE_NOTE if last else "")
        + _BUDGET_NOTE
    )


def _escape_tail(trace: object, escapes: int, reason: str) -> dict:
    return {
        "trace": _trace_block(trace),
        "stopped_because": str(reason or "")[:600],
        "instruction": _ESCAPE_INSTRUCTION,
        **_escape_counts(escapes),
        "worker_instructions": _escape_worker_instructions(escapes),
    }


# The escape-hatch packet: the trace, the NEW screen, continue from here.
#
# Keyword-only ``ctx``: ``run_id`` and ``tc_id`` (str), ``escapes`` (int) are
# required; ``reason`` (str) is optional.
def build_escape_job(
    view: object, screen: object, trace: object, **ctx: str | int
) -> dict:
    try:
        run_id = str(ctx["run_id"])
        escapes = int(ctx["escapes"])
        reason = str(ctx.get("reason", ""))
        packet = _packet_base(run_id, str(ctx["tc_id"]))
        packet.update(
            {
                "kind": "escape",
                **_turn_fields(view, screen, run_id),
                **_escape_tail(trace, escapes, reason),
            }
        )
        return packet
    except _PACKET_ERRORS:  # pragma: no cover - defensive
        logger.warning("build_escape_job failed", exc_info=True)
        return {}
