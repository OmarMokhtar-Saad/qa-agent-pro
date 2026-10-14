"""The step-assertion gate: refuse steps whose expected_result restates the action."""

from __future__ import annotations

import logging
from typing import NamedTuple

from agents.host_category_calls import AssertionGateIn
from config.settings_guard import never_raise
from handlers import _common, export
from tools import prep_store, step_assertion

logger = logging.getLogger("tools.mcp_handlers")

_EVENTS = {
    "step_assertion_ack": "mcp_submit_suite_assertion_override",
    "step_assertion": "mcp_submit_suite_refused",
}

_REFUSAL_HEAD = (
    "⛔ **Submission refused:** in the "
    "categor(ies) below, most steps have an `expected_result` "
    "that asserts nothing the `action` did not already say -- "
    "typically a restatement of the action itself. A step like "
    "that passes against correct software AND against broken "
    "software, so executing it measures nothing.\n\n"
)


class _Refusal(NamedTuple):
    """The facts a step-assertion refusal body is built from."""

    over: list
    findings: list
    first_beat: str
    ack_shape: str


async def _mark_assertion_refused(prep_id: str, envelope: dict, meta: dict) -> None:
    """Two-beat ack: stamp the prep so a LATER step_assertion_ack is honoured.
    Non-fatal: an unpersisted mark only means the next ack is refused again."""
    with never_raise(logger, "assertion_refused mark failed", exc_info=True):
        _mark = await prep_store.update_prep(
            prep_id,
            {**envelope, "meta": {**meta, "assertion_refused": True}},
        )
        if (_mark or {}).get("error"):
            logger.warning(
                "prep %s not marked assertion_refused (%s)",
                prep_id,
                (_mark or {}).get("error"),
            )


async def _assertion_ack_shape(prep_id: str) -> str:
    """How the tester finalizes as-is: with rows staged the ack alone is the
    whole retry; otherwise nothing extra is said."""
    rows = ((await prep_store.load_submissions(prep_id)) or {}).get("content") or []
    if not rows:
        return ""
    return (
        " and "
        + export._finalize_route_phrase(short=True)
        + " -- the staged categories are held server-side, so do not resend them"
    )


def _over_detail(over: list) -> str:
    """One bullet per category over the threshold."""
    return "\n".join(
        f"- **{name}**: {hit} of {total} step(s) ({ratio:.0%})"
        for name, hit, total, ratio in over
    )


def _assertion_refusal_text(prep_id: str, refusal: _Refusal) -> str:
    """The step-assertion refusal body (the caller prefixes its version note)."""
    return (
        _REFUSAL_HEAD + f"{_over_detail(refusal.over)}\n\n"
        f"{step_assertion.step_assertion_detail(refusal.findings)}\n\n"
        "Rewrite those expected results to name the concrete "
        "observable outcome -- the exact on-screen message, the "
        "field/button state, or the resulting screen -- and resubmit "
        f"under the SAME prep_id `{prep_id}`. **Nothing was "
        "discarded**: the prep and every staged category row "
        "survive. To finalize as-is "
        "anyway, ask the tester first and then resend with "
        f"`step_assertion_ack=true`{refusal.ack_shape}.{refusal.first_beat}"
    )


def _override_note(count: int) -> str:
    """The reply disclosure when the tester overrode the gate."""
    return (
        "> ⚠️  Finalized with `step_assertion_ack=true` over "
        f"{count} step(s) whose expected_result asserts "
        "nothing the action did not already state.\n\n"
    )


def _first_beat(ack: bool) -> str:
    """The note that an ack on the FIRST submit was ignored, else nothing."""
    if not ack:
        return ""
    return (
        "\n\n> ⚠️  `step_assertion_ack=true` arrived on the "
        "FIRST submit for this prep and was IGNORED -- the finding "
        "below had not been shown to anyone yet."
    )


async def _audit_assertion(prep_id: str, reason: str, detail: dict) -> None:
    """Audit an overridden or refused step-assertion gate."""
    await _common.audit(
        _EVENTS[reason],
        entity_id=prep_id,
        detail={"reason": reason, **detail},
    )


async def step_assertion_gate(args: AssertionGateIn) -> tuple[str | None, str]:
    """F4 (2026-08-29): the ENFORCEMENT gate against steps that restate the action.

    Same ``(refusal, note)`` contract as ``volume_floor_gate``; placed beside it
    and before finalize for the same reason: a refusal costs one round trip and
    destroys nothing."""
    prep_id, envelope, findings = args.prep_id, args.envelope, args.findings
    ack = args.ack
    over = step_assertion.categories_over_threshold(args.all_cases)
    if not over:
        return None, ""
    meta = envelope.get("meta") or {}
    detail = {"categories": [row[0] for row in over], "flagged_steps": len(findings)}
    if ack and bool(meta.get("assertion_refused")):
        await _audit_assertion(prep_id, "step_assertion_ack", detail)
        return None, _override_note(len(findings))
    await _mark_assertion_refused(prep_id, envelope, meta)
    await _audit_assertion(prep_id, "step_assertion", detail)
    refusal = _Refusal(over, findings, _first_beat(ack), ack_shape="")
    return await _refusal_with_shape(prep_id, refusal), ""


async def _refusal_with_shape(prep_id: str, refusal: _Refusal) -> str:
    """The refusal body once the ack shape for *prep_id* is filled in."""
    shape = await _assertion_ack_shape(prep_id)
    return _assertion_refusal_text(prep_id, refusal._replace(ack_shape=shape))
