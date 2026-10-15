"""The replay's last raw dump, held for the next packet until anything could have moved the screen.

A replay ends on a dump taken AFTER its last action; the explore turn that
follows used to take another one (about two seconds on a real phone) of a
screen that had not changed. This holds that dump per device and serves it once
while it is provably current.

INVALIDATION IS GENERATION-BASED and lives at the one place every device
command passes, ``adb_run.run_argv``: any command that is not a known
read-only one bumps the device's generation (before AND after it runs), so a
tap, a type, a swipe, a key, a launch, an install or anything a later tool
adds is covered without a list of ops. A dump stores itself only when the
generation it started under is still current when it finishes. The age bound
covers what no adb call can see: the tester touching the phone.

Keyed per device serial; one serial is one run at a time (the device lock).
"""

from __future__ import annotations

import copy
import time

#: Seconds a held dump may stand for the next read. The next packet is built
#: within one call of the replay, so a few seconds is the real window; past it
#: a reused screen is a guess (the tester can touch the phone).
REUSE_MAX_AGE_S = 3.0

#: Devices whose last dump is held; the oldest is dropped first.
MAX_HELD_SERIALS = 16

#: What a device command may start with and still leave the screen as it is.
READ_ONLY_PREFIXES = (
    "uiautomator dump",
    "dumpsys",
    "cat ",
    "getprop",
    "wm size",
    "wm density",
    "settings get",
    "pm list",
    "pm path",
    "screencap",
    "ls ",
)

#: Host-side adb verbs that never touch the screen.
READ_ONLY_VERBS = ("devices", "get-state", "version", "start-server", "get-serialno")

_GEN: dict[str, int] = {}
_HELD: dict[str, tuple] = {}


def _serial_of(cmd: list[str]) -> str:
    return cmd[cmd.index("-s") + 1] if "-s" in cmd[:-1] else ""


def _body_of(cmd: list[str]) -> str:
    for verb in ("shell", "exec-out"):
        if verb in cmd:
            return " ".join(cmd[cmd.index(verb) + 1 :]).strip()
    return ""


def is_read_only(cmd: list[str]) -> bool:
    """True only for a command known not to change the screen."""
    body = _body_of(cmd)
    if body:
        return body.startswith(READ_ONLY_PREFIXES)
    return any(v in cmd[1:] for v in READ_ONLY_VERBS)


def note_command(cmd: list[str]) -> None:
    """Called around every adb command: an unknown or actuating one ends reuse."""
    if is_read_only(cmd):
        return
    serial = _serial_of(cmd)
    if serial:
        bump(serial)
        return
    for key in set(_GEN) | set(_HELD):
        bump(key)


def bump(serial: str) -> None:
    """Something that can change the screen ran (or is about to) on *serial*."""
    key = str(serial or "")
    _GEN[key] = _GEN.get(key, 0) + 1
    _HELD.pop(key, None)


def token(serial: str) -> int:
    """The generation a dump about to start runs under."""
    return _GEN.get(str(serial or ""), 0)


def store(serial: str, started_under: int, result: dict) -> None:
    """Hold a successful dump, unless anything actuated since it started."""
    key = str(serial or "")
    if result.get("error") or started_under != token(key):
        return
    _HELD.pop(key, None)
    _HELD[key] = (copy.deepcopy(result), time.monotonic(), started_under)
    while len(_HELD) > MAX_HELD_SERIALS:
        del _HELD[next(iter(_HELD))]


def take(serial: str) -> dict | None:
    """The held dump when young and nothing actuated since; used at most once."""
    key = str(serial or "")
    held = _HELD.pop(key, None)
    if held is None or held[2] != token(key):
        return None
    if time.monotonic() - held[1] > REUSE_MAX_AGE_S:
        return None
    return held[0]


def reset() -> None:
    """Forget every held dump and generation (tests; a process-global cache)."""
    _GEN.clear()
    _HELD.clear()
