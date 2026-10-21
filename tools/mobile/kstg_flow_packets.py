"""Cutting a learned flow into replayable packets (see ``knowledge_stage_flows``).

One saved flow replays as one script, so it holds at most ``MAX_MODEL_ACTIONS``
steps and must fit the wait budget on its own. A longer run is saved as
``<base>-p1`` .. ``<base>-pN`` instead of being cut short; the model replays them
in order. The wait caps count each condition wait's ``max_s`` ceiling, not the time
it really takes, so a packet closes when its ceilings add up to the cap. It also
closes where the steps switch between the app and a runtime-permission dialog
(``kstg_flow_perm``), so the login and the permission part replay on their own.
"""

from __future__ import annotations

import re

from tools.mobile import actions, kstg_flow_build, kstg_flow_perm

#: Most packets one goal may become (4 x MAX_MODEL_ACTIONS = MAX_ACTIONS steps).
MAX_FLOW_PACKETS = 4
#: A run that reached its goal may take as many as the names allow (-p1 .. -p9).
MAX_REACHED_PACKETS = 9
_SUFFIX_ROOM = 3  # "-p" plus one digit
PACKET_RE = re.compile(r"^(?P<base>.+)-p(?P<n>[1-9][0-9]*)$")


def _fits(acts: list) -> bool:
    if len(acts) > actions.MAX_MODEL_ACTIONS:
        return False
    return not actions.parse_script({"actions": acts}).get("error")


def chunk(acts: list) -> list:
    """Replayable packets covering the usable PREFIX of *acts*.

    The prefix ends before the first action that cannot stand alone as a script.
    """
    packets: list = []
    cur: list = []
    seen = ""
    for act in acts:
        cut, seen = kstg_flow_perm.boundary(seen, act)
        if cur and (cut or not _fits(cur + [act])):
            packets.append(cur)
            cur = []
        if not _fits(cur + [act]):
            break
        cur.append(act)
    return packets + [cur] if cur else packets


def replayable_from(recs: list) -> list:
    """*recs* without the leading steps that cannot stand alone as a script.

    A route whose first step is an unnamed tap (nothing stable to aim at) is not refused
    whole: the replay starts at the first step that can run by itself, and the screen
    each step carries still tells the server where that start is.
    """
    for at, rec in enumerate(recs):
        if _fits([rec["flow_action"]]):
            return recs[at:]
    return []


def kept(packets: list) -> int:
    """How many actions the *packets* hold."""
    return sum(len(p) for p in packets)


def names(goal: str, count: int, sig: str = "") -> list:
    """The flow name of each of *count* packets (*sig*: the route signature)."""
    if count <= 1:
        return [kstg_flow_build.flow_name(goal, 0, sig)]
    base = kstg_flow_build.flow_name(goal, _SUFFIX_ROOM, sig)
    return ["%s-p%d" % (base, n) for n in range(1, count + 1)]
