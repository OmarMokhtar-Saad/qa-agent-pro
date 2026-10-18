"""Auto-replay: once a flow's anchor holds, the optional steps after it are dropped.

A recorded ``wait_until_text`` is the flow's anchor: the screen it names is where the
flow was going. The optional steps after it (a permission popup, a wait for a popup
to go, an idle wait) only matter if such a popup is really there, or if a required
step further on needs the screen clear. So after the anchor holds on the live screen
the run of optional steps is skipped when

* no optional step in the run names text that is on the live screen (a popup that is
  showing is still handled by running the run), and
* the run ends the flow, or the required step that follows it already has its one
  target on the live screen.

Anything else runs as recorded, so a popup that blocks a later step is still met.
Pure over the template and the live screen; never raises.
"""

from __future__ import annotations

from typing import Any

from tools.mobile import flow_replay_match_anchor
from tools.mobile.replay.screen_has import _screen_has

ANCHOR = "wait_until_text"
#: Optional steps: may do nothing when their popup is absent.
SKIPPABLE = frozenset({"tap_text_if_present", "wait_until_gone", "wait_until_idle"})
#: Skippable steps that name text a popup would show.
NAMED = frozenset({"tap_text_if_present", "wait_until_gone"})


def is_anchor(step: dict) -> bool:
    """True for a wait for text: the screen the flow was heading to."""
    return step.get("op") == ANCHOR and bool(step.get("text"))


def ends_batch(steps: list, at: int) -> bool:
    """True when the batch should end after step *at*: an anchor, then optional steps."""
    follows = at + 1 < len(steps) and steps[at + 1].get("op") in SKIPPABLE
    return is_anchor(steps[at]) and follows


def _run_end(steps: list, pos: int) -> int:
    end = pos
    while end < len(steps) and steps[end].get("op") in SKIPPABLE:
        end += 1
    return end


def _popup_showing(steps: list, span: range, screen: Any) -> bool:
    return any(
        steps[i].get("op") in NAMED and _screen_has(screen, steps[i].get("text"))
        for i in span
    )


def skip_to(template: dict, pos: int, screen: Any) -> int:
    """The step to continue at after the batch that ended at *pos*, on *screen*."""
    steps = template.get("steps") or []
    if not (0 < pos <= len(steps)) or not is_anchor(steps[pos - 1]):
        return pos
    if not _screen_has(screen, steps[pos - 1].get("text")):
        return pos
    end = _run_end(steps, pos)
    if end == pos or _popup_showing(steps, range(pos, end), screen):
        return pos
    if end >= len(steps) or flow_replay_match_anchor.target_unique(steps[end], screen):
        return end
    return pos
