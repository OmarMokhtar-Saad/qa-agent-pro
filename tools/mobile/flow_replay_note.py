"""The note an auto-replay leaves for the model (see ``flow_replay``).

It says what the replay DID, step by step as ``op target -> landed screen``; what it did
NOT do (the steps left); and, when it stopped, where it diverged: the step, the screen
the flow expected and the live one. Screen keys are shortened. A secret or typed value is
never repeated. Pure; never raises.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from tools.mobile.flow_replay_run import MISSING_INPUT
from tools.mobile.flow_replay_stop import DID_NOT_RUN
from tools.untrusted import single_line

KEY_CHARS = 12
#: What the host may do when a replay stops (B14): the stop is the host's decision.
OPTIONS = (
    "Options: continue from this screen yourself; if a tester input is named above, "
    "send it in tester_inputs and the replay resumes at this step; or say flow=none "
    "to stop auto-replay."
)
MAX_LISTED = 8
MAX_GOAL = 80
MAX_TARGET = 60
MAX_NAME = 40
#: The note's first words; the reply renderers hoist a note that starts with it.
OPEN = "Before your steps"


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
    goal = single_line((template.get("meta") or {}).get("goal"), MAX_GOAL).strip()
    return "%s (goal: %s)" % (name, goal) if goal else name


def _target(step: dict) -> str:
    got = step.get("target")
    target: dict = got if isinstance(got, dict) else {}
    named = next((str(v) for v in target.values() if v), "")
    text = named or ("" if step.get("secret") else str(step.get("text") or ""))
    return single_line(text, MAX_TARGET if named else 30).strip()


def _line_head(step: dict) -> str:
    """``op target`` without a trailing space when the step has no target."""
    return ("%s %s" % (step.get("op"), _target(step))).strip()


def _line(template: dict, at: int) -> str:
    steps = template.get("steps") or []
    after = (template.get("meta") or {}).get("after") or []
    land = after[at] if at < len(after) else ""
    return "%d. %s" % (at + 1, _line_head(steps[at])) + (
        " -> " + short(land) if land else ""
    )


def done_lines(head: Head, pos: int) -> list:
    """The replayed steps, newest last, capped to :data:`MAX_LISTED`."""
    return [_line(head.template, i) for i in range(head.start, pos)][-MAX_LISTED:]


def _kind(why: str) -> str:
    """Name the failure: ``device error``, ``missing value`` or ``screen differs``."""
    low = why.lower()
    if low.startswith(MISSING_INPUT):
        return "missing value"
    if "device_error" in low or low.startswith(DID_NOT_RUN):
        return "device error"
    return "screen differs"


def _diverged(head: Head, pos: int, why: str) -> str:
    before = (head.template.get("meta") or {}).get("before") or []
    want = before[pos] if pos < len(before) else ""
    kind = _kind(why)
    lead = "stopped at step %d (%s): %s" % (pos + 1, kind, single_line(why, 200))
    if kind != "screen differs":
        return lead
    return "diverged at step %d: %s (expected screen %s, live screen %s)" % (
        pos + 1,
        single_line(why, 200),
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


CLOSERS = (
    "close", "dismiss", "cancel", "got it", "×",
    "no, later", "not now", "skip", "later", "no thanks", "ok",
)  # fmt: skip
CLOSE_WORDS = ("close", "dismiss")


def _has_close_word(low: str) -> bool:
    """Whether a lower-cased label has a whole close word (``close_icon``, not ``disclose``)."""
    return any(t in CLOSE_WORDS for t in re.split(r"[^a-z0-9]+", low))


def _label_closes(step: dict) -> bool:
    """Whether the tapped control reads as a close control (a guess, never proof)."""
    got = step.get("target")
    target: dict = got if isinstance(got, dict) else {}
    for key, value in target.items():
        low = str(value or "").strip().lower()
        if low in CLOSERS or (key != "text" and _has_close_word(low)):
            return True
    return False


def _closes(step: dict) -> bool:
    """Whether *step* may have closed a sheet, dialog or popup (back, or a close-like tap)."""
    op = str(step.get("op") or "")
    if op == "back":
        return True
    return op.startswith("tap") and "present" not in op and _label_closes(step)


def closed_lines(head: Head, pos: int) -> list:
    """One hedged note line per replayed step that may have closed a sheet or dialog."""
    steps = head.template.get("steps") or []
    return [
        "It may have closed a sheet, dialog or popup at step %d (%s)."
        % (i + 1, _line_head(steps[i]))
        for i in range(head.start, min(pos, len(steps)))
        if _closes(steps[i])
    ][:MAX_LISTED]


def _opening(head: Head, pos: int, why: str) -> str:
    """The first sentence: what ran before the host's steps (E-04)."""
    named = labelled("'%s'" % single_line(head.name, MAX_NAME), head.template)
    if pos <= head.start:
        return "%s, saved flow %s was tried but ran no steps." % (OPEN, named)
    ran = "; ".join(done_lines(head, pos))
    done = "" if why else ", " + _landed(head, pos)
    return "%s, saved flow %s ran %s%s: %s." % (
        OPEN,
        named,
        _span(head, pos),
        done,
        ran,
    )


def _stopped(head: Head, pos: int, why: str) -> str:
    """The stop tail: the steps left, where it diverged, and the host's options."""
    total = len(head.template.get("steps") or [])
    skipped = "\nnot replayed: %d step(s) left (from step %d)." % (total - pos, pos + 1)
    return skipped + "\n%s. %s" % (_diverged(head, pos, why), OPTIONS)


def text(head: Head, pos: int, why: str) -> str:
    """The whole note for a replay that ran steps ``head.start`` .. *pos* - 1.

    It OPENS with what ran before the host's own steps (E-04).
    """
    extra = "".join("\n" + x for x in closed_lines(head, pos))
    for said in head.said:
        extra += "\n" + single_line(said, 200) + "."
    return _opening(head, pos, why) + extra + (_stopped(head, pos, why) if why else "")
