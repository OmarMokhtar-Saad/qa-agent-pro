"""Exploratory turn packets."""

from __future__ import annotations

from tools.mobile import actions as actions_mod
from tools.mobile import charter as charter_mod
from tools.mobile import perception
from tools.mobile.run_packets_args import ExploreRef
from tools.mobile.run_packets_base import (
    _PACKET_ERRORS,
    _observation_key,
    _packet_base,
    _screen_block,
    logger,
)
from tools.mobile.run_packets_text import _EXPLORE_INSTRUCTION
from tools.untrusted import wrap_untrusted


def _explore_goal(body: dict) -> dict:
    return {
        "goal": wrap_untrusted("goal", str(body.get("goal") or ""), limit=1200),
        # Fix round 3, item 1. The full goal above goes to a chat ONCE
        # per run (mcp_handlers elides it with `render.without_goal`);
        # `sub_goal` is the model's own current step and rides every
        # packet, wrapped because it is model-authored text coming
        # back. `app` is the server-checked hand-over package.
        "sub_goal": wrap_untrusted(
            "sub_goal", str(body.get("sub_goal") or ""), limit=400
        ),
        "app": str(body.get("app") or "")[:200],
    }


def _explore_budget(body: dict, remaining: object) -> dict:
    left = remaining if isinstance(remaining, dict) else {}
    return {
        "watch_for": list(body.get("watch_for") or []),
        "turn": int(body.get("turn") or 0),
        "turns_left": int(left.get("turns") or 0),
        "seconds_left": int(left.get("seconds") or 0),
        "extensions_left": max(0, 1 - int(body.get("extensions_used") or 0)),
        "guard_destructive": bool(body.get("guard", True)),
    }


def _explore_steering(body: dict) -> dict:
    """THE CHARTER'S THREE STEERING FIELDS. They ride the state
    this packet is already built from (``state["charter"]``), so
    no signature moves. ``depth`` decides what the packet ASKS
    THE MODEL TO ATTEMPT -- one server-owned sentence, not a
    tactics catalogue -- and the scope lines are WRAPPED, like
    ``goal``, because they are tester-authored text
    reaching a model.
    """
    return {
        "depth": charter_mod.normalize(body.get("charter"))["depth"],
        "attempt": charter_mod.depth_directive(body.get("charter")),
        "scope": wrap_untrusted(
            "scope",
            charter_mod.scope_sentence(body.get("charter")),
            limit=1200,
        ),
    }


# READ, never re-derived. ``explore_runner.next_turn`` is the
# ONE writer of this verdict, computed once from the screen it
# has just pruned; deriving it again here would be two
# conditions over one question, and mirrored conditions drift.
#
# TWO KEYS ONLY: ``state`` and ``note`` are SERVER-authored
# constants. ``matched`` is the tester's own charter line --
# the same provenance as ``scope``, which is wrapped --
# so it does NOT ride the packet as a bare string. The choice
# is to KEEP IT ON THE STATE rather than wrap it here: it is
# already recorded in ``off_charter`` where the tester and a
# resume read it, the model does not need to be told WHICH of
# the lines it was already shown matched in order to leave the
# screen, and one wrapped value is cheaper than two. One class
# of string, one route: tester text reaching a model is
# wrapped, or it does not go.
def _explore_scope_verdict(body: dict) -> dict:
    return {
        "state": str((body.get("scope_verdict") or {}).get("state") or ""),
        "note": str((body.get("scope_verdict") or {}).get("note") or ""),
    }


def _explore_state(state: object, remaining: object) -> dict:
    body = state if isinstance(state, dict) else {}
    return {
        **_explore_goal(body),
        **_explore_budget(body, remaining),
        **_explore_steering(body),
        "scope_verdict": _explore_scope_verdict(body),
    }


def _explore_response_schema() -> dict:
    """The turn fields come from `actions.TURN_FIELD_SCHEMA`, the
    same table `decode_reply` splits on. They were literals here,
    and v1.79.0 shipped a release in which this packet advertised
    `finding` while the transport refused it -- one source is
    what stops the two drifting again.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["actions"],
        "properties": dict(
            {
                "actions": actions_mod.response_schema()
                .get("properties", {})
                .get("actions", {"type": "array"})
            },
            **actions_mod.TURN_FIELD_SCHEMA,
        ),
    }


def _explore_worker_instructions() -> str:
    return (
        "Spend turns, not actions: one script that finishes a whole "
        "round costs one turn, and four scripts that each do one "
        "step of it cost four. "
        "Record anything a tester would want to know in `finding`, "
        "one sentence, even when the turn went fine. "
        "Set `sub_goal` to the one step you are on now: it goes INSIDE "
        "the `script` JSON, "
        '{"actions": [...], "sub_goal": "..."}, never as a tool '
        "argument. End each script with an assert action that checks what "
        "the turn proved, then call done(); a turn that asserts nothing "
        "has no verdict. When the goal moves on to ANOTHER app and that "
        "app is on screen, set `app` the same way, once."
    )


def build_explore_turn(state: object, screen: object, ref: ExploreRef) -> dict:
    """One exploratory turn packet."""
    try:
        packet = _packet_base(ref.run_id)
        packet.update(
            {
                "kind": "explore",
                "observation_id": _observation_key(screen),
                "screen_unusable": perception.dump_unusable(screen),
                **_explore_state(state, ref.remaining),
                "screen_block": _screen_block(screen, ref.run_id),
                "instruction": _EXPLORE_INSTRUCTION,
                "response_schema": _explore_response_schema(),
                "worker_instructions": _explore_worker_instructions(),
            }
        )
        return packet
    except _PACKET_ERRORS:  # pragma: no cover - defensive
        logger.warning("build_explore_turn failed", exc_info=True)
        return {}
