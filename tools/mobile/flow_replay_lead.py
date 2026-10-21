"""A batch never opens with a full-budget wait for text the live screen does not show.

A recorded ``wait_until_text`` at the head of a batch waits for text of the OLD run: on
a different screen it only burns its full timeout (the 15 s "My File" wait). It may
also be a legitimate "page still loading" wait, so it is kept with a short bounded
timeout (``LEAD_WAIT_S``): a slow first screen still gets a chance, a stale wait is
cheap. Pure.
"""

from __future__ import annotations

from tools.mobile.replay.screen_has import _screen_has

#: Seconds a leading text wait absent from the live screen may still wait.
LEAD_WAIT_S = 3


def _stale(step: dict, screen: object) -> bool:
    """True for a text wait whose text is absent from a live *screen* that has elements."""
    live = isinstance(screen, dict) and bool(screen.get("elements"))
    if not live or step.get("op") != "wait_until_text":
        return False
    return not _screen_has(screen, str(step.get("text") or ""))


def _capped(step: dict) -> dict:
    own = float(step.get("max_s") or LEAD_WAIT_S)
    return dict(step, max_s=min(own, LEAD_WAIT_S))


def cap(steps: list, screen: object) -> list:
    """*steps* with the stale text waits at their head cut to ``LEAD_WAIT_S`` seconds."""
    out = list(steps)
    for at, step in enumerate(out):
        if not _stale(step, screen):
            break
        out[at] = _capped(step)
    return out
