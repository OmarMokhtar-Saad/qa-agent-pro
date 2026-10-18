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

from tools.mobile import dump_ime_neutral, dump_neutral

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

#: Seconds a refused script's reply may stand on the last dump: nothing was
#: replayed, so the screen is the one the model already read.
REFUSED_MAX_AGE_S = 20.0

_GEN: dict[str, int] = {}
_HELD: dict[str, tuple] = {}
_LAST: dict[str, tuple] = {}
_REFUSED: list[tuple[float, str]] = []
_SHOT_SKIP: list[str] = []


def _serial_of(cmd: list[str]) -> str:
    return cmd[cmd.index("-s") + 1] if "-s" in cmd[:-1] else ""


def body_of(cmd: list[str]) -> str:
    for verb in ("shell", "exec-out"):
        if verb in cmd:
            return " ".join(cmd[cmd.index(verb) + 1 :]).strip()
    return ""


def is_read_only(cmd: list[str]) -> bool:
    """True only for a command known not to change the screen."""
    body = body_of(cmd)
    if body:
        return body.startswith(READ_ONLY_PREFIXES) or dump_neutral.neutral_body(body)
    return any(v in cmd[1:] for v in READ_ONLY_VERBS) or dump_neutral.neutral_pull(cmd)


def note_command(cmd: list[str]) -> None:
    """Called around every adb command: an unknown or actuating one ends reuse."""
    if is_read_only(cmd):
        return
    serial = _serial_of(cmd)
    if serial and _keyboard_switch_is_neutral(serial, cmd):
        return
    if serial:
        bump(serial)
        return
    for key in set(_GEN) | set(_HELD):
        bump(key)


def _keyboard_switch_is_neutral(serial: str, cmd: list[str]) -> bool:
    """A keyboard switch while no held dump shows a keyboard leaves them valid."""
    entries = (_HELD.get(serial), _LAST.get(serial))
    held = [e[0].get("content") for e in entries if e]
    return dump_ime_neutral.keeps_held(serial, body_of(cmd), held)


def bump(serial: str) -> None:
    """Something that can change the screen ran (or is about to) on *serial*."""
    key = str(serial or "")
    _GEN[key] = _GEN.get(key, 0) + 1
    _HELD.pop(key, None)
    _LAST.pop(key, None)


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
    _LAST[key] = _HELD[key]
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


def take_aged(serial: str) -> tuple[dict, float] | None:
    """``(dump, age_s)`` of the held dump when nothing actuated since; used at most once.

    For a caller that validates an older dump itself (the focus probe); the
    plain :func:`take` age bound does not apply here.
    """
    key = str(serial or "")
    held = _HELD.pop(key, None)
    if held is None or held[2] != token(key):
        return None
    return held[0], time.monotonic() - held[1]


def _only_fresh_serial(now: float) -> str:
    """The one serial with a recent dump; "" when none or several (ambiguous)."""
    fresh = [k for k, v in _LAST.items() if now - v[1] <= 4 * REFUSED_MAX_AGE_S]
    return fresh[0] if len(fresh) == 1 else ""


def note_refusal() -> None:
    """A script was refused before any replay: the next read may reuse the last dump.

    A refusal does not name its device, so it is pinned to the one serial with a
    recent dump; with several devices live it marks nothing (a fresh dump runs).
    """
    now = time.monotonic()
    serial = _only_fresh_serial(now)
    _REFUSED[:] = [(now, serial)] if serial else []


def take_after_refusal(serial: str) -> dict | None:
    """The last dump when *serial* was just refused and nothing actuated since; once."""
    marked, marked_serial = _REFUSED[0] if _REFUSED else (0.0, "")
    _REFUSED.clear()
    held = _LAST.get(str(serial or ""))
    now = time.monotonic()
    if marked_serial != str(serial or "") or held is None:
        return None
    if now - marked > REFUSED_MAX_AGE_S:
        return None
    if held[2] != token(serial) or now - held[1] > 4 * REFUSED_MAX_AGE_S:
        return None
    _SHOT_SKIP[:] = [str(serial or "")]
    return copy.deepcopy(held[0])


def consume_shot_skip(serial: str) -> bool:
    """True once when this serial's dump was just re-served after a refusal."""
    hit = bool(_SHOT_SKIP) and _SHOT_SKIP[0] == str(serial or "")
    _SHOT_SKIP.clear()
    return hit


def reset() -> None:
    """Forget every held dump and generation (tests; a process-global cache)."""
    _GEN.clear()
    _HELD.clear()
    _LAST.clear()
    _REFUSED.clear()
    _SHOT_SKIP.clear()
