"""Auto-replay: switch to another saved flow when the app takes a branch.

A replay that stops on the wrong screen (a one-time interstitial such as a biometric
or permission prompt) does not hand the turn to the model at once: the chain re-picks
a saved flow from the live screen (the stopped flow is already in ``done``), runs it,
then resumes the stopped flow if its next step now fits the screen. The switches are
bounded and every step still passes the landing gate and the destructive guard.
"""

from __future__ import annotations

from typing import Any

from tools.mobile import flow_replay_match, flow_replay_run

#: Detours one turn may take (each is one other flow, so no loops).
MAX_SWITCHES = 2
_PENDING = "auto_pending"
_COUNT = "auto_switches"


def detour(body: dict, at: tuple, why: str) -> bool:
    """True when a stop on the wrong screen should try another flow; remembers *at*
    (``(name, pos)``) as the flow to resume (the first one stopped is kept)."""
    if not why.startswith(flow_replay_run.WRONG_SCREEN):
        return False
    if int(body.get(_COUNT) or 0) >= MAX_SWITCHES:
        return False
    body[_COUNT] = int(body.get(_COUNT) or 0) + 1
    body.setdefault(_PENDING, {"name": at[0], "pos": int(at[1])})
    return True


def resume(body: dict, key: Any, package: str) -> tuple:
    """``(name, template, pos)`` of the stopped flow when it fits *key* now, else empty.

    Taken once: the pending mark is cleared when the flow is handed back.
    """
    held = body.get(_PENDING) or {}
    name, pos = str(held.get("name") or ""), int(held.get("pos") or 0)
    got = flow_replay_match.template(package, name) if name else {}
    if got and flow_replay_match.starts(got, key, pos):
        body.pop(_PENDING, None)
        return name, got, pos
    return "", {}, 0


def clear(body: dict) -> None:
    """Forget the pending flow and the switch count (a new turn starts clean)."""
    body.pop(_PENDING, None)
    body.pop(_COUNT, None)
