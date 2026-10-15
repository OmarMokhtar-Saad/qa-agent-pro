"""Cutting a learned flow into replayable packets (see ``knowledge_stage_flows``).

One saved flow replays as one script, so it holds at most ``MAX_MODEL_ACTIONS``
steps and must fit the wait budget on its own. A longer run is saved as
``<base>-p1`` .. ``<base>-pN`` instead of being cut short; the model replays them
in order. The wait caps count each condition wait's ``max_s`` ceiling, not the time
it really takes, so a packet closes when its ceilings add up to the cap.
"""

from __future__ import annotations

import re

from tools.mobile import actions, kstg_flow_build

#: Most packets one goal may become (4 x MAX_MODEL_ACTIONS = MAX_ACTIONS steps).
MAX_FLOW_PACKETS = 4
_SUFFIX_ROOM = 3  # "-p" plus one digit
PACKET_RE = re.compile(r"^(?P<base>.+)-p(?P<n>[1-9][0-9]*)$")


def _fits(acts: list) -> bool:
    if len(acts) > actions.MAX_MODEL_ACTIONS:
        return False
    return not actions.parse_script({"actions": acts}).get("error")


def chunk(acts: list) -> list:
    """Replayable packets covering every action, or ``[]`` when one cannot stand alone."""
    packets: list = []
    cur: list = []
    for act in acts:
        if cur and not _fits(cur + [act]):
            packets.append(cur)
            cur = []
        cur.append(act)
        if not _fits(cur):
            return []
    return packets + [cur] if cur else packets


def names(goal: str, count: int) -> list:
    """The flow name of each of *count* packets."""
    if count <= 1:
        return [kstg_flow_build.flow_name(goal)]
    base = kstg_flow_build.flow_name(goal, _SUFFIX_ROOM)
    return ["%s-p%d" % (base, n) for n in range(1, count + 1)]
