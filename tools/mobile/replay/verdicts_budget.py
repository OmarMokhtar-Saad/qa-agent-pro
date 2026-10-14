"""Replay engine: budget-stop verdicts and the serialised queue. Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

from typing import Any, NamedTuple

from tools.mobile import actions as actions_mod
from tools.mobile.replay.context import SUBMIT_BUDGET_S, Context


# This replay's wall-clock bound, always positive.
#
# A zero or negative budget would stop the FIRST action, so the replay would
# make no progress and every submit would spend an escape for nothing. An
# unusable value falls back to the module default rather than deadlocking the
# case.
def _budget_seconds(ctx: Context) -> float:
    """This replay's wall-clock bound, always positive."""
    try:
        value = float(getattr(ctx, "budget_s", None) or SUBMIT_BUDGET_S)
    except (TypeError, ValueError, OverflowError):
        value = SUBMIT_BUDGET_S
    return value if value > 0 else SUBMIT_BUDGET_S


class QueueTail(NamedTuple):
    """How much of a budget-stopped replay's tail is queued, or that it is a route."""

    queued: int | None = None
    route: bool = False


def _route_budget_stop_reason(ran: int, remaining: int) -> str:
    """``budget_stop_reason`` for a saved route, which queues nothing."""
    return (
        "budget reached after "
        + str(int(ran))
        + " action(s) of this saved route; the remaining "
        + str(remaining)
        + " were NOT run and NOT queued, because a route step runs only "
        "after its screen is checked against the saved one: carry on from "
        "the screen below and send the steps still needed."
    )


def _kept_count(queued: int | None, remaining: int) -> int:
    """How many of the *remaining* actions the queue really holds."""
    return remaining if queued is None else max(0, min(int(queued), remaining))


def _all_queued_text(ran: int, remaining: int) -> str:
    """The stop text when the whole remainder is queued."""
    return (
        "budget reached after "
        + str(int(ran))
        + " action(s); the remaining "
        + str(remaining)
        + " action(s) are queued and will run FIRST on your next submit -- "
        "you do not need to resend them."
    )


def _partly_queued_text(ran: int, total: int, kept: int) -> str:
    """The stop text when a credential literal cut the queue short."""
    first_dropped = int(ran) + kept + 1
    return (
        "budget reached after "
        + str(int(ran))
        + " action(s); of the remaining "
        + str(max(0, int(total) - int(ran)))
        + ", "
        + str(kept)
        + " are queued and will run FIRST on your next submit -- do not resend "
        "those. Actions "
        + str(first_dropped)
        + " to "
        + str(int(total))
        + " of this script were NOT queued, because action "
        + str(first_dropped)
        + " types a value at a credential field and that value is never stored: "
        "send them again, with the value, in your next submit."
    )


def _kept_text(ran: int, total: int, kept: int) -> str:
    """Whole-queue text when nothing was dropped, else the partly-queued text."""
    remaining = max(0, int(total) - int(ran))
    if kept == remaining:
        return _all_queued_text(ran, remaining)
    return _partly_queued_text(ran, total, kept)


# What the model is told when the wall clock ended a replay early.
#
# ``tail.queued`` is how many of the remaining actions the queue really holds.
# ``_serialize_queued`` ends the queue at a literal typed at a credential
# field, so telling the model "do not resend" for the dropped tail would
# skip those steps silently: they are named and asked for again instead.
# ``tail.route`` is a saved-route replay, which queues nothing: its remainder
# has not been checked against the saved screens, so it is asked for again.
def budget_stop_reason(ran: int, total: int, tail: QueueTail = QueueTail()) -> str:
    """What the model is told when the wall clock ended a replay early."""
    remaining = max(0, int(total) - int(ran))
    limit = " One submit replays for at most " + str(int(SUBMIT_BUDGET_S)) + "s."
    if tail.route:
        return _route_budget_stop_reason(ran, remaining) + limit
    kept = _kept_count(tail.queued, remaining)
    return _kept_text(ran, total, kept) + limit


# Unreached actions from a budget-stopped replay, as plain dicts ready to
# run again on a later submit -- the SERVER-SIDE queue this defect exists to
# carry, instead of asking the model to retype a shorter script.
#
# A queued ``type`` action never carries a secret literal past this call:
# the FIELD NAME survives (so the tester supplies it again on the submit
# that finally runs it, exactly like a fresh secret field); the VALUE does
# not.
#
# A NON-secret action that types a literal at a credential-named field ends
# the queue: it and everything after it are dropped, and the model issues
# them again on the next submit. Blanking the literal instead would queue a
# step that cannot replay (``type`` needs text), and keeping it would write
# a credential in clear to the run folder and the reply.
def _serialize_queued(pending: Any) -> list[dict]:
    """Unreached actions from a budget-stopped replay, as plain dicts."""
    out: list[dict] = []
    for action in list(pending or []):
        try:
            payload = action.model_dump(mode="json")
        except (AttributeError, TypeError, ValueError):  # pragma: no cover
            continue
        if payload.get("secret"):
            payload["text"] = ""
        elif payload.get("text") and actions_mod.is_credential_action(action):
            break
        out.append(payload)
    return out
