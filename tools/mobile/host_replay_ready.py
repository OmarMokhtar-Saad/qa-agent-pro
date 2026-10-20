"""Which saved flow fits the live screen, and which of its inputs the tester still owes.

Split out of ``host_auto_replay``. B15: a flow that fits but lacks inputs used to be
silent, so the host spent turns finding out what to send. The names (never values)
now go in the first packet.
"""

from __future__ import annotations

from typing import Any

from tools.mobile import (
    flow_replay_inputs,
    flow_replay_match,
    flow_replay_visited,
    held_inputs,
)
from tools.mobile.case_run_types import _Ref
from tools.mobile.flow_replay_note import MAX_LISTED


def fitting(ref: _Ref, screen: Any, body: dict) -> dict:
    """The saved flow whose first step fits the live screen, else ``{}``.

    B13: the pinned package, the screen's and every package this run visited.
    """
    ctx = ref.ctx
    key = flow_replay_match.live_keys(screen, ctx)
    found = flow_replay_visited.pick_first((ref.run_id, ctx, screen), key, body)
    return found[2]


def others(ref: _Ref, screen: Any, body: dict) -> list:
    """B17: names of the other saved flows starting on this screen, for the host to pick."""
    key = flow_replay_match.live_keys(screen, ref.ctx)
    return flow_replay_visited.alternatives((ref.run_id, ref.ctx, screen), key, body)


def choose_note(names: list) -> str:
    """The packet line listing the other flows that fit; the host may run one by name."""
    return (
        "other saved flows also start on this screen: "
        + ", ".join(names[:MAX_LISTED])
        + ". Run one instead with flow=<name>, or say flow=none to stop auto-replay."
    )


def missing(ref: _Ref, template: dict) -> list:
    """Input NAMES the flow needs that neither the tester nor this run has given."""
    have = dict(
        held_inputs.recall(ref.run_id),
        **(getattr(ref.ctx, "tester_inputs", None) or {}),
    )
    got = flow_replay_inputs.resolve(template, have)
    return [n for n in flow_replay_inputs.wanted(template) if n not in got]


def ask_note(names: list) -> str:
    """The packet line asking for *names*; values are never echoed or stored here."""
    listed = ", ".join(names)
    return (
        "a saved flow fits this screen and would replay now; it needs these "
        f"inputs in tester_inputs on your next submit: {listed}"
    )
