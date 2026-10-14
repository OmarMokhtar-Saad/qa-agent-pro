"""The identity of a watch: the run prefix, the serial guard and the watch id.

``network_watch`` re-exports every name here.
"""

from __future__ import annotations

import re
from pathlib import Path

#: The pseudo-run identity a desktop watch borrows. It is NOT a run id from the
#: mobile lane: a watch has no case, no script and no report, and reusing a real
#: run's id would file desktop observations under somebody's test run.
WATCH_RUN_PREFIX = "desktop-watch"

#: What a device serial may look like: adb serials (`emulator-5554`,
#: `R5CT30ABCDE`, `192.168.1.20:5555`) and nothing that can name a path. A
#: serial reaches a directory name AND an rmtree in this module, so the guard
#: is the SHAPE of the token, not a prefix on the id built from it: the prefix
#: check downstream still passes `desktop-watch-../../x`.
_SERIAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


def _key(serial: str) -> str:
    """The serial as a safe token, or "" when it is not one.

    Empty means "refuse": every caller already treats an empty key as a
    missing serial, so a traversal-shaped serial is refused by the same path,
    and the refusal text names the shape that was expected.
    """
    text = str(serial or "").strip()
    if not text or not _SERIAL_RE.match(text) or text.strip(".") == "":
        return ""
    return text


def _serial_refusal(serial: str) -> str:
    if not str(serial or "").strip():
        return "a device serial is required"
    return (
        "Refusing serial %r: a device serial is letters, digits and . _ : - only "
        "(as `adb devices` prints it). It names a directory here, so anything "
        "path-shaped is refused by name." % str(serial)[:80]
    )


def _under(child: Path, parent: Path) -> bool:
    """True when ``child`` resolves inside ``parent``. The second lock on the door."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def _watch_id(serial: str) -> str:
    return WATCH_RUN_PREFIX + "-" + (_key(serial) or "unknown")
