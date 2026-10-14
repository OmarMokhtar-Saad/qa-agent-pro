"""The foreground wait's bounds, its refusal text, and the sleep it can be given."""

from __future__ import annotations

import asyncio

from tools.mobile import run_store
from tools.mobile.replay.context import Context

#: How long to wait for the app to actually reach the FOREGROUND after the
#: clean launch, and how often to look. `adb.launch` returns as soon as the
#: intent is delivered, not when the app is drawn -- so the first dump raced it
#: and a case opened on whatever was in front before. On 2026-09-06 that was
#: the Settings app on its Reset options page, and the planning packet offered
#: a model "Erase all data (factory reset)" as the place to begin.
FOREGROUND_TIMEOUT_S = 8.0
FOREGROUND_POLL_S = 0.5

#: How many dumps that wait may make, WHATEVER THE CLOCK SAYS. The deadline is
#: the bound that should fire; this is the one that cannot be argued out of
#: firing. A deadline is arithmetic on a clock, and a deadline recomputed inside
#: the loop is never reached -- the wait then spins forever, which no test can
#: turn red: it HANGS, and a hung job has no attribution. A count is monotonic
#: by construction. ``FOREGROUND_TIMEOUT_S / FOREGROUND_POLL_S`` is 16, so this
#: only binds once the clock has stopped telling the truth.
MAX_FOREGROUND_POLLS = 24


def _foreground_refusal(seen: str, expected: str) -> str:
    return (
        "The app did not come to the foreground: after launching "
        + str(expected)[:80]
        + " the screen still belongs to "
        + str(seen or "an unknown package")[:80]
        + ". Nothing was planned against it -- a case that starts on another "
        "app's screen plans against that app. Bring "
        + str(expected)[:80]
        + " to the front and run the case again."
    )


async def _sleep(seconds: float) -> None:
    """Named so a test can replace it; a real sleep is the slowest thing here."""
    await asyncio.sleep(seconds)


def _start_refusal(tc_id: object, ctx: Context) -> dict | None:
    """The error payload for a case that must not start, or None."""
    if not run_store.valid_tc_id(tc_id):
        return {
            "error": (
                "Refusing to run case id "
                + repr(str(tc_id)[:40])
                + "; it must look like TC-001."
            ),
            "content": None,
        }
    if not ctx.package:
        return {
            "error": "No app package is set for this run; nothing was launched.",
            "content": None,
        }
    return None
