"""Clip commands that leave the screen alone, matched by exact shape.

The media lane runs ``pkill -INT screenrecord`` (or ``kill -INT``, then
``kill -9`` when it is ignored, of one recorder's pid), ``rm -f`` of its own clip and
``adb pull`` of that clip after every replay. None of them touches the display,
yet an unknown command ends dump reuse (see :mod:`tools.mobile.dump_reuse`), so
each replay threw away the dump the next read could have used. Only these
shapes are neutral: any other ``rm`` or ``pull``, and every tap, type, key or
swipe, still counts as an actuation.
"""

from __future__ import annotations

import re

from tools.mobile.media_consts import REMOTE_DIR

_CLIP = re.escape(REMOTE_DIR) + r"/qa-agents-[A-Za-z0-9_.-]+\.mp4"

#: Device shell bodies (after ``shell``) that only start, stop or delete a clip.
_BODIES = (
    re.compile(r"pkill -INT screenrecord"),
    re.compile(r"kill -(?:INT|9) [1-9][0-9]{0,7}"),
    re.compile(r"rm -f " + _CLIP),
    re.compile(
        r"(?:echo \$\$; exec )?screenrecord --time-limit \d+ --bit-rate \S+ --size \S+ "
        + _CLIP
    ),
)

_PULL = re.compile(_CLIP)


def neutral_body(body: str) -> bool:
    """True for a device shell body that is exactly a clip command."""
    return any(shape.fullmatch(body) for shape in _BODIES)


def neutral_pull(cmd: list[str]) -> bool:
    """True for a host-side ``adb pull <clip on the device> <local file>``."""
    if "shell" in cmd or "pull" not in cmd[1:]:
        return False
    tail = cmd[cmd.index("pull") + 1 :]
    return len(tail) == 2 and bool(_PULL.fullmatch(tail[0]))
