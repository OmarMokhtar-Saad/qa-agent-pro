"""The reply of an adopting call whose setup is still running past the cap."""

from __future__ import annotations

import time

_STILL = (
    "The setup from your previous call is still running (started {at}, "
    "{age} s ago); it was not lost.\n\n"
    "Options:\n"
    "- call this tool again: it picks the same setup up and answers when it ends;\n"
    "- `qa_mobile_status` shows where it is;\n"
    "- change an argument (device, app or source) to drop it and start fresh."
)


def still_running(started: float) -> str:
    at = time.strftime("%H:%M:%S", time.localtime(started))
    return _STILL.format(at=at, age=int(time.time() - started))
