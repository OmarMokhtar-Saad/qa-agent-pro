"""Proof that the qa-ime in use is the pinned build, and that its event clock works.

After an install the device is asked once for the package's ``versionName``; a build
other than the pinned one is noted ``ime_version_mismatch:<got>``. A fast a11y dump
that carries no event clock is noted ``ime_clock_missing:<versionName>``, and a quiet
wait that cannot run for the same reason ``quiet_wait_unavailable``. Each note is
said once per run on a serial (``begin_run`` starts the count again and reads the
version when no install has). Notes only land while a step breakdown is armed.
"""

from __future__ import annotations

from tools.mobile import a11y_events, imes_upgrade, step_timing
from tools.mobile.ime_host import host

#: ``versionName`` last read per serial; ``""`` when it could not be read.
_VERSION: dict = {}
#: ``(serial, note name)`` already said.
_SAID: set = set()
#: run id last begun per serial.
_RUN: dict = {}


def reset() -> None:
    """Forget every version, said note and run. For tests."""
    _VERSION.clear()
    _SAID.clear()
    _RUN.clear()


def note_once(serial: str, reason: str) -> None:
    """Ledger *reason* the first time per serial, and only while a step is armed."""
    token = (str(serial or ""), reason.split(":", 1)[0])
    if token in _SAID or not step_timing.summary():
        return
    _SAID.add(token)
    step_timing.note(reason)


async def begin_run(serial: str, run_id: str) -> None:
    """A new run on *serial*: its notes may be said again; read the version once."""
    key = str(serial or "")
    if _RUN.get(key) == run_id:
        return
    _RUN[key] = run_id
    _SAID.difference_update({t for t in _SAID if t[0] == key})
    if key not in _VERSION:
        await check_installed(serial)


async def check_installed(serial: str) -> None:
    """Read the installed ``versionName``; note it when it is not the pinned one."""
    key = str(serial or "")
    info = host().manifest().get("content") or {}
    package, want = str(info.get("package") or ""), str(info.get("version") or "")
    if not package:
        return
    got = await imes_upgrade.installed_version(serial, package)
    _VERSION[key] = got
    if got and want and got != want:
        note_once(serial, "ime_version_mismatch:" + got)


def on_dump(serial: str) -> None:
    """After a successful a11y dump: note a missing event clock."""
    if a11y_events.latest(serial) is None:
        got = _VERSION.get(str(serial or "")) or "unknown"
        note_once(serial, "ime_clock_missing:" + got)
