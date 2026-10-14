"""What a dead emulator process said on its way out (B5)."""

from __future__ import annotations

import os

from tools.mobile import emulator_state
from tools.mobile.emulator_host import host

#: Characters of the emulator's own output quoted back to the tester.
LOG_TAIL_CHARS = 600


def _read_tail(path: str) -> bytes:
    """The last ``LOG_TAIL_CHARS * 4`` bytes of *path*, ``b""`` if unreadable."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - LOG_TAIL_CHARS * 4))
            return fh.read()
    except OSError:
        return b""


def _log_tail(path: str) -> str:
    """The informative end of an emulator log: error-ish lines first. Never raises."""
    if not path:
        return ""
    raw = _read_tail(path)
    text = "".join(
        c for c in raw.decode("utf-8", errors="replace") if c.isprintable() or c == "\n"
    )
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    errs = [
        ln
        for ln in lines
        if any(k in ln.upper() for k in ("ERROR", "PANIC", "FATAL", "FAILED"))
    ]
    return " | ".join((errs or lines)[-6:])[-LOG_TAIL_CHARS:]


def _exit_fix(tail: str) -> str:
    """A fix chosen from the emulator's own words; the generic one is last."""
    low = tail.lower()
    if any(k in low for k in ("whpx", "haxm", "hvf", "kvm", "hypervisor", "accel")):
        return host().accel_fix(tail)
    if any(k in low for k in ("already running", "same avd", "multiple emulators")):
        return "Another emulator is already using this AVD. Close its window (or run `adb -s <serial> emu kill`), then retry."
    if any(
        k in low
        for k in ("unknown avd", "no such avd", "could not find avd", "cannot find avd")
    ):
        return "The emulator does not know this AVD name. Run `emulator -list-avds` and pass one of those names."
    if "no space" in low or "disk" in low:
        return "The disk is full or read-only. Free space under the Android AVD folder, then retry."
    return "Start this AVD once from Android Studio's Device Manager to see the same error in its window, then fix what it names."


def _exited(name: str) -> tuple[dict, int] | None:
    """``(run, exit code)`` for *name*'s emulator if its process has ended."""
    run = host().RUNS.get(name)
    if not run:
        return None
    proc = host().PROCS.get(int(run.get("pid") or 0))
    code = None if proc is None else proc.poll()
    return None if code is None else (run, code)


def _error_text(head: str, tail: str, log: object) -> str:
    """The tester-facing sentence: the head, the quoted output, the fix, the log."""
    return (
        head
        + host()._own_output(tail)
        + f"Fix: {_exit_fix(tail)} Full log: {log or 'not captured'}"
    )


# None while *avd*'s emulator is running or unknown; else what it said when it
# died: ``{"avd", "code", "log", "error"}``. One-shot: reporting CONSUMES the
# record, so the next start is not answered with the last failure. A zero exit
# with nothing error-like in the log is not reported (on Windows the launcher
# may hand off to a child and exit 0).
_BEFORE_LISTED = "before it appeared in `adb devices`"


def exit_report(avd: str, *, when: str = _BEFORE_LISTED) -> dict | None:
    """What *avd*'s emulator said when it died, or None while it runs."""
    name = str(avd or "").strip()[:120]
    found = _exited(name)
    if found is None:
        return None
    run, code = found
    log = str(run.get("log") or "")
    tail = _log_tail(log)
    if code == 0 and not tail:
        return None
    host().RUNS.pop(name, None)
    emulator_state._STARTED.pop(name, None)
    head = f"{name}'s emulator exited with code {code} {when}. "
    return {
        "avd": name,
        "code": int(code),
        "log": log,
        "error": _error_text(head, tail, log),
    }
