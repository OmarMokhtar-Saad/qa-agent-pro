"""Emulator lifecycle: boot detached, wait for a real boot, re-attach, stop.

Two things here are lessons rather than features.

**Re-attach by AVD NAME.** The MCP server restarts far more often than an
emulator does (every code or ``.env`` edit), so "is one of my AVDs already
running?" must be answerable without state. ``adb -s <serial> emu avd name``
answers it from the device itself, so a resumed run finds the emulator it left
behind instead of booting a second one.

**adb-first-on-PATH.** Two adb binaries of different versions fight: each kills
the other's server and every command intermittently reports "device offline".
:func:`ensure_adb_first_on_path` puts the SDK's adb first for this process and
reports what was shadowing it.
"""

from __future__ import annotations

import asyncio  # noqa: F401  (patch target)
import logging
import shutil  # noqa: F401  (patch target)
import subprocess
import sys
import time  # noqa: F401  (patch target)

from tools.mobile import adb, emulator_host, paths  # noqa: F401
from tools.mobile.emulator_accel import (  # noqa: F401
    ACCEL_CHECK_TIMEOUT_S,
    _accel_verdict,
    accel_check,
    accel_check_sync,
    accel_fix,
    diagnose_no_devices,
    render_no_devices_hint,
)
from tools.mobile.emulator_boot import (  # noqa: F401
    Launch,
    _booted,
    _reattach,
    _spawn_and_wait,
    boot,
)
from tools.mobile.emulator_exit import (  # noqa: F401
    LOG_TAIL_CHARS,
    _exit_fix,
    _log_tail,
    exit_report,
)
from tools.mobile.emulator_probe import (  # noqa: F401
    avd_name_of,
    find_running,
    list_avds,
    list_avds_sync,
    list_running,
)
from tools.mobile.emulator_start import (  # noqa: F401
    _spawn_command,
    _spawn_kwargs,
    ensure_adb_first_on_path,
    start,
    stop,
)
from tools.mobile.emulator_state import (  # noqa: F401
    _STARTED,
    _emulator_log_path,
    clear_started,
    note_started,
    recently_started,
)
from tools.mobile.emulator_wait import (  # noqa: F401
    BOOT_PROP,
    BOOT_SERIAL_POLL_S,
    POLL_INTERVAL_S,
    BootOpts,
    _await_serial,
    _boot_timeout_error,
    _never_listed_error,
    boot_timeout_s,
    wait_boot,
)
from tools.untrusted import wrap_untrusted

logger = logging.getLogger(__name__)

#: Fix round 3, item 5. AVDs THIS process spawned, name -> monotonic start
#: time. On the Air run ``qa_list_devices`` said "No devices detected" while the
#: emulator ``qa_mobile_test`` had just started was still booting: early in a
#: boot adb does not list the device at all, so the only evidence is that we
#: started it. :func:`start` is the one spawn site, so it is the one writer.
#:
#: Process memory, bounded by :data:`MAX_BOOT_RECORDS` and aged out after
#: :data:`BOOT_RECORD_TTL_S`. No PID liveness check: ``os.kill(pid, 0)``
#: TERMINATES the process on Windows. A crashed emulator therefore reads as
#: "started N s ago and not visible to adb yet" until its record ages out --
#: which is exactly what the tester is told.
MAX_BOOT_RECORDS = 8
BOOT_RECORD_TTL_S = 600

#: pid -> Popen for the last few emulators this process spawned, so the boot poll
#: can ask "did it already die?" (Batch 1b, B5). Bounded by MAX_BOOT_RECORDS.
PROCS: dict[int, subprocess.Popen] = {}

#: avd -> {"pid", "log"} for the emulators this process spawned (B5). Bounded by
#: MAX_BOOT_RECORDS, oldest dropped first.
RUNS: dict[str, dict] = {}


def _trim_procs() -> None:
    """Drop the oldest process records beyond the bound."""
    while len(PROCS) > MAX_BOOT_RECORDS:
        PROCS.pop(next(iter(PROCS)), None)


def _trim_runs() -> None:
    """Drop the oldest run records beyond the bound."""
    while len(RUNS) > MAX_BOOT_RECORDS:
        RUNS.pop(next(iter(RUNS)), None)


def _open_log(kwargs: dict):
    """Pop ``log_path`` and, when openable, point stdout/stderr at it."""
    log_path = str(kwargs.pop("log_path", "") or "")
    if not log_path:
        return None
    try:
        handle = open(log_path, "wb")  # noqa: SIM115 - closed by spawn's finally
    except OSError:
        logger.debug("mobile.emulator: cannot open %s", log_path, exc_info=True)
        return None
    kwargs["stdout"] = handle
    kwargs["stderr"] = subprocess.STDOUT
    return handle


# The detached-spawn seam. Returns the child pid.
#
# ``log_path`` is popped (it is not a Popen argument): when given, the child's
# stdout AND stderr go to that file, TRUNCATED on every spawn so the log cannot
# grow across starts. The emulator prints its own reason for refusing to start
# there, which used to go to DEVNULL (B5). The parent closes its handle at
# once; the child keeps its duplicate.
def spawn(cmd: list[str], **kwargs) -> int:
    """The detached-spawn seam. Returns the child pid."""
    handle = _open_log(kwargs)
    try:
        proc = subprocess.Popen(cmd, **kwargs)  # noqa: S603 - argv list, no shell
    finally:
        if handle is not None:
            handle.close()
    PROCS[int(proc.pid)] = proc
    _trim_procs()
    return int(proc.pid)


def _note_run(avd: str, pid: int, log: str) -> None:
    """Remember which pid/log belongs to *avd*. Never raises."""
    name = str(avd or "").strip()[:120]
    if not name:
        return
    RUNS.pop(name, None)
    RUNS[name] = {"pid": int(pid or 0), "log": str(log or "")}
    _trim_runs()


def _own_output(tail: str) -> str:
    """The emulator's own words, quoted back as untrusted text."""
    return f"Its own output: {wrap_untrusted('emulator log', tail, limit=600) or 'nothing was captured'}. "


# `python_is_windows()` USED TO LIVE HERE and was deleted: a sixth spelling of
# `sys.platform == "win32"` whose only caller in the whole tree was a test
# asserting it existed. `platform_info.is_windows()` is the one producer, and
# `tests/mobile/test_mobile_platform_support.py` fails by file name if a second
# one reappears anywhere under `tools/mobile/`.

emulator_host.bind(sys.modules[__name__])
