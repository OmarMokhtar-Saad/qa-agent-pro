"""The note an auto-replay leaves for the model (see ``flow_replay``).

It says what the replay DID, step by step as ``op target -> landed screen``; what it did
NOT do (the steps left); and, when it stopped, where it diverged: the step, the screen
the flow expected and the live one. Screen keys are shortened. A secret or typed value is
never repeated. Pure; never raises.
"""

from __future__ import annotations

from typing import NamedTuple

KEY_CHARS = 12
#: What the host may do when a replay stops (B14): the stop is the host's decision.
OPTIONS = (
    "Options: continue from this screen yourself; if a tester input is named above, "
    "send it in tester_inputs and the replay resumes at this step; or say flow=none "
    "to stop auto-replay."
)
MAX_LISTED = 8
MAX_GOAL = 80


class Head(NamedTuple):
    """The flow a replay ran: name, template, the step it began at, the live key."""

    name: str
    template: dict
    start: int
    live: str
    said: tuple = ()


def short(key: object) -> str:
    return str(key or "")[:KEY_CHARS] or "?"


def labelled(name: str, template: dict) -> str:
    """*name* plus the flow's own goal text (B17: advisory, lets the host spot a mismatch)."""
    goal = " ".join(str((template.get("meta") or {}).get("goal") or "").split())
    return "%s (goal: %s)" % (name, goal[:MAX_GOAL]) if goal else name


def _target(step: dict) -> str:
    got = step.get("target")
    target: dict = got if isinstance(got, dict) else {}
    named = next((str(v) for v in target.values() if v), "")
    return named or ("" if step.get("secret") else str(step.get("text") or ""))[:30]


def _line(template: dict, at: int) -> str:
    steps = template.get("steps") or []
    after = (template.get("meta") or {}).get("after") or []
    land = after[at] if at < len(after) else ""
    head = "%d. %s %s" % (at + 1, steps[at].get("op"), _target(steps[at]))
    return head + (" -> " + short(land) if land else "")


def done_lines(head: Head, pos: int) -> list:
    """The replayed steps, newest last, capped to :data:`MAX_LISTED`."""
    return [_line(head.template, i) for i in range(head.start, pos)][-MAX_LISTED:]


def _diverged(head: Head, pos: int, why: str) -> str:
    before = (head.template.get("meta") or {}).get("before") or []
    want = before[pos] if pos < len(before) else ""
    return "diverged at step %d: %s (expected screen %s, live screen %s)" % (
        pos + 1,
        why,
        short(want),
        short(head.live),
    )


def _span(head: Head, pos: int) -> str:
    """``steps A-B of N`` (1-based): the steps replayed, not just where it ended."""
    total = len(head.template.get("steps") or [])
    first = min(head.start + 1, pos)
    if first == pos:
        return "step %d of %d" % (pos, total)
    return "steps %d-%d of %d" % (first, pos, total)


def _landed(head: Head, pos: int) -> str:
    """``all landed`` only for a replay of the whole flow; else just the ran steps."""
    whole = head.start == 0 and pos == len(head.template.get("steps") or [])
    return "all landed" if whole else "each replayed step landed"


def text(head: Head, pos: int, why: str) -> str:
    """The whole note for a replay that ran steps ``head.start`` .. *pos* - 1."""
    total = len(head.template.get("steps") or [])
    named = labelled("`%s`" % head.name, head.template)
    base = "auto-replayed flow %s: %s" % (named, _span(head, pos))
    ran = done_lines(head, pos)
    steps = "\nreplayed: " + "; ".join(ran) if ran else ""
    for said in head.said:
        steps += "\n" + said + "."
    if not why:
        return "%s, %s.%s" % (base, _landed(head, pos), steps)
    left = total - pos
    skipped = "\nnot replayed: %d step(s) left (from step %d)." % (left, pos + 1)
    stop = "\n%s. %s" % (_diverged(head, pos, why), OPTIONS)
    return base + "." + steps + skipped + stop
