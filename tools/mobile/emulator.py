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

import asyncio
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from config.settings import settings
from tools.mobile import (
    adb,
    paths,
    platform_info,
    sdk_locator,
    watch,
)
from tools.untrusted import wrap_untrusted

logger = logging.getLogger(__name__)

#: How often the boot poll asks the device. Half a second (S16): a boot that
#: finished, or an emulator that died, is seen within one short poll.
POLL_INTERVAL_S = 0.5

#: How often ``boot()`` asks adb whether the new emulator has a serial yet.
#: Slower than :data:`POLL_INTERVAL_S` on purpose: a cold emulator takes tens
#: of seconds to appear, each ask is an ``adb devices`` round trip, and a
#: process that died is caught by ``exit_report`` on the same tick.
BOOT_SERIAL_POLL_S = 2.0

#: A booted device answers this with ``1``.
BOOT_PROP = "sys.boot_completed"


def boot_timeout_s() -> int:
    """Operator-tunable boot budget (``QA_MOBILE_BOOT_TIMEOUT_S``)."""
    try:
        return max(30, int(settings.qa_mobile_boot_timeout_s))
    except Exception:  # pragma: no cover - coercer guarantees an int
        return 240


#: pid -> Popen for the last few emulators this process spawned, so the boot poll
#: can ask "did it already die?" (Batch 1b, B5). Bounded by MAX_BOOT_RECORDS.
_PROCS: dict[int, subprocess.Popen] = {}


def _spawn(cmd: list[str], **kwargs) -> int:
    """The detached-spawn seam. Returns the child pid.

    ``log_path`` is popped (it is not a Popen argument): when given, the child's
    stdout AND stderr go to that file, TRUNCATED on every spawn so the log cannot
    grow across starts. The emulator prints its own reason for refusing to start
    there, which used to go to DEVNULL (B5). The parent closes its handle at
    once; the child keeps its duplicate.
    """
    log_path = str(kwargs.pop("log_path", "") or "")
    handle = None
    if log_path:
        try:
            handle = open(log_path, "wb")  # noqa: SIM115 - closed in the finally below
        except OSError:
            logger.debug("mobile.emulator: cannot open %s", log_path, exc_info=True)
        else:
            kwargs["stdout"] = handle
            kwargs["stderr"] = subprocess.STDOUT
    try:
        proc = subprocess.Popen(cmd, **kwargs)  # noqa: S603 - argv list, no shell
    finally:
        if handle is not None:
            handle.close()
    _PROCS[int(proc.pid)] = proc
    while len(_PROCS) > MAX_BOOT_RECORDS:
        _PROCS.pop(next(iter(_PROCS)), None)
    return int(proc.pid)


def ensure_adb_first_on_path(adb_path: str = "") -> dict:
    """Put the SDK's adb first on this process's PATH.

    ``{"error", "content": {"adb", "changed", "shadowed_by"}}``. Only this
    process's environment is touched -- nothing is written to a shell profile,
    because a tool that edits a tester's dotfiles to fix its own problem is
    worse than the problem.
    """
    try:
        resolved = str(adb_path or "") or adb.resolve_adb()
        if not resolved or not Path(resolved).is_file():
            return {
                "error": None,
                "content": {"adb": resolved, "changed": False, "shadowed_by": ""},
            }
        first = shutil.which(platform_info.exe("adb")) or ""
        if first and os.path.realpath(first) == os.path.realpath(resolved):
            return {
                "error": None,
                "content": {"adb": resolved, "changed": False, "shadowed_by": ""},
            }
        bin_dir = str(Path(resolved).parent)
        os.environ["PATH"] = bin_dir + os.pathsep + (os.environ.get("PATH") or "")
        return {
            "error": None,
            "content": {"adb": resolved, "changed": True, "shadowed_by": first},
        }
    except Exception as exc:
        logger.exception("mobile.emulator.ensure_adb_first_on_path failed")
        return {"error": str(exc), "content": None}


def _list_avds_sync() -> dict:
    """AVD names ``emulator -list-avds`` reports. Blocking; see :func:`list_avds`.

    Newer emulators print ``INFO | ...`` diagnostics on the same stdout, and an
    AVD name never contains a space or ``|``, so a line with either is not a
    name. No emulator binary is an ERROR, never ``[]``: "no SDK" and "no AVD"
    get two different setup guides.
    """
    try:
        located = (sdk_locator.locate_sdk() or {}).get("content") or {}
        binary = str((located.get("tools") or {}).get("emulator") or "")
        if not binary:
            return {
                "error": (
                    "The Android emulator binary was not found. Install Android "
                    "Studio, then create an emulator in its Device Manager."
                ),
                "content": None,
            }
        rc, out, err = platform_info._run_sync([binary, "-list-avds"], timeout=60)
        if rc != 0:
            return {
                "error": "emulator -list-avds failed: "
                + (err.strip() or "rc=" + str(rc))[:300],
                "content": None,
            }
        names = [
            line.strip()
            for line in out.splitlines()
            if line.strip() and " " not in line.strip() and "|" not in line
        ]
        return {"error": None, "content": names}
    except Exception as exc:
        logger.exception("mobile.emulator.list_avds failed")
        return {"error": str(exc), "content": None}


async def list_avds() -> dict:
    """AVD names, asked on a worker thread.

    The subprocess would otherwise hold the event loop for up to its own 60 s
    timeout, and a caller's ``asyncio.wait_for`` (qa-doctor's) could not bound
    it.
    """
    return await asyncio.to_thread(_list_avds_sync)


async def avd_name_of(serial: str) -> dict:
    """The AVD name behind an ``emulator-*`` serial, or ``""``."""
    result = await adb.raw(["-s", str(serial), "emu", "avd", "name"], timeout=15)
    if result.get("error"):
        return {"error": None, "content": ""}
    lines = [
        line.strip()
        for line in str((result["content"] or {}).get("out") or "").splitlines()
        if line.strip() and line.strip().upper() != "OK"
    ]
    return {"error": None, "content": lines[0] if lines else ""}


async def find_running(avd: str) -> dict:
    """``{"serial": ...}`` for an already-running *avd*, serial ``""`` if none."""
    try:
        listed = await adb.devices()
        if listed.get("error"):
            # NOT `serial: ""`. A failed probe is not the same fact as "no
            # emulator is running", and callers that spawn on the second answer
            # would otherwise start a device because adb was unreachable.
            return {"error": str(listed["error"]), "content": None}
        for serial in listed.get("content") or []:
            if not str(serial).startswith("emulator-"):
                continue
            named = await avd_name_of(serial)
            if str((named or {}).get("content") or "") == str(avd):
                return {"error": None, "content": {"serial": serial, "avd": str(avd)}}
        return {"error": None, "content": {"serial": "", "avd": str(avd)}}
    except Exception as exc:
        logger.exception("mobile.emulator.find_running failed")
        return {"error": str(exc), "content": None}


async def list_running() -> dict:
    """Every booted ``emulator-*`` serial on this machine, with its AVD name.

    Unlike :func:`find_running`, this does NOT filter by AVD -- it is how the
    device-selection stage sees a FOREIGN emulator (one not named
    by the caller) that a tester already has running, so a run can adopt it
    instead of spawning one.

    ``{"error", "content": [{"serial": ..., "avd": ...}, ...]}``.
    """
    try:
        listed = await adb.devices()
        if listed.get("error"):
            # An empty list means "nothing is booted", which sends the device
            # stage on to provisioning and a spawn. A broken adb must not be
            # able to say that: it reported "The emulator is still starting"
            # while starting a SECOND emulator over the tester's own (D1,
            # 2026-09-03). The caller already branches on `error`.
            return {"error": str(listed["error"]), "content": None}
        out: list[dict] = []
        for serial in listed.get("content") or []:
            if not str(serial).startswith("emulator-"):
                continue
            named = await avd_name_of(serial)
            out.append(
                {"serial": serial, "avd": str((named or {}).get("content") or "")}
            )
        return {"error": None, "content": out}
    except Exception as exc:
        logger.exception("mobile.emulator.list_running failed")
        return {"error": str(exc), "content": None}


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
_STARTED: dict[str, float] = {}


def note_started(avd: str, *, now: float | None = None) -> None:
    """Record that *avd* was spawned just now. Never raises."""
    import time

    try:
        name = str(avd or "").strip()[:120]
        if not name:
            return
        _STARTED[name] = time.monotonic() if now is None else float(now)
        while len(_STARTED) > MAX_BOOT_RECORDS:
            _STARTED.pop(min(_STARTED, key=_STARTED.__getitem__), None)
    except Exception:  # pragma: no cover - defensive
        logger.debug("mobile.emulator.note_started failed", exc_info=True)


def recently_started(*, now: float | None = None) -> list[dict]:
    """``[{"avd", "age_s"}]`` for spawns younger than the TTL, oldest first.

    Two kinds of reader: one that found the device list empty and wants to say
    an emulator is still booting, and ``avd_manage``'s delete guard, which
    reads it unconditionally so an AVD that was just started is not removed.
    Never raises.
    """
    import time

    try:
        stamp = time.monotonic() if now is None else float(now)
        for name in [n for n, t in _STARTED.items() if stamp - t > BOOT_RECORD_TTL_S]:
            _STARTED.pop(name, None)
        return [
            {"avd": name, "age_s": max(0, int(stamp - started))}
            for name, started in sorted(_STARTED.items(), key=lambda kv: kv[1])
        ]
    except Exception:  # pragma: no cover - defensive
        logger.debug("mobile.emulator.recently_started failed", exc_info=True)
        return []


def clear_started() -> None:
    """Forget every record. For tests."""
    _STARTED.clear()
    _RUNS.clear()
    _PROCS.clear()


#: avd -> {"pid", "log"} for the emulators this process spawned (B5). Bounded by
#: MAX_BOOT_RECORDS, oldest dropped first.
_RUNS: dict[str, dict] = {}

#: Characters of the emulator's own output quoted back to the tester.
LOG_TAIL_CHARS = 600

#: Seconds `emulator -accel-check` may run (B3). The Windows feature query in
#: platform_info takes up to 60 s and is deliberately NOT used to explain an
#: empty device list.
ACCEL_CHECK_TIMEOUT_S = 10


def _emulator_log_path(avd: str) -> str:
    """``state/emulator-<avd>.log``, or ``""`` when the tree is unavailable.

    There is no run folder yet when an emulator is spawned, so the log lives in
    the shared ``state/`` dir; the file is truncated on every spawn (see
    :func:`_spawn`).
    """
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in str(avd))[:60]
    try:
        return str(paths.state_file("emulator-" + safe + ".log"))
    except Exception:
        logger.debug("mobile.emulator: no log path", exc_info=True)
        return ""


def _note_run(avd: str, pid: int, log: str) -> None:
    """Remember which pid/log belongs to *avd*. Never raises."""
    name = str(avd or "").strip()[:120]
    if not name:
        return
    _RUNS.pop(name, None)
    _RUNS[name] = {"pid": int(pid or 0), "log": str(log or "")}
    while len(_RUNS) > MAX_BOOT_RECORDS:
        _RUNS.pop(next(iter(_RUNS)), None)


def _log_tail(path: str) -> str:
    """The informative end of an emulator log: error-ish lines first. Never raises."""
    if not path:
        return ""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - LOG_TAIL_CHARS * 4))
            raw = fh.read()
    except OSError:
        return ""
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


def accel_fix(output: str = "") -> str:
    """The per-platform fix for a failed hardware-acceleration check.

    Uses the fix TEXT only. ``platform_info.virtualization()`` and the privilege
    probe are not called: they can take a minute, and this runs while a tester
    waits for an empty device list to be explained.
    """
    if platform_info.is_windows():
        return platform_info._WHPX_FIX
    if platform_info.is_macos():
        return platform_info._HVF_FIX
    return (
        "On Linux the emulator needs KVM. Check `ls -l /dev/kvm`, install your "
        "distribution's qemu-kvm / cpu-checker package, make sure virtualization is "
        "enabled in the BIOS/UEFI, and add your user to the `kvm` group "
        "(`sudo usermod -aG kvm $USER`, then log out and back in)."
    )


def _accel_check_sync() -> dict:
    """Bounded ``emulator -accel-check``. ``content.ok`` is None when undetermined."""
    try:
        located = (sdk_locator.locate_sdk() or {}).get("content") or {}
        binary = str((located.get("tools") or {}).get("emulator") or "")
        if not binary:
            return {
                "error": "The Android emulator binary was not found.",
                "content": None,
            }
        rc, out, err = platform_info._run_sync(
            [binary, "-accel-check"], timeout=ACCEL_CHECK_TIMEOUT_S
        )
        text = " ".join((out + "\n" + err).split())[:400]
        if rc == 124:
            return {
                "error": None,
                "content": {"ok": None, "timed_out": True, "output": text, "fix": ""},
            }
        if rc in (126, 127):
            return {
                "error": text or "emulator -accel-check could not run",
                "content": None,
            }
        ok = rc == 0
        return {
            "error": None,
            "content": {
                "ok": ok,
                "timed_out": False,
                "output": text,
                "fix": "" if ok else accel_fix(text),
            },
        }
    except Exception as exc:
        logger.exception("mobile.emulator.accel_check failed")
        return {"error": str(exc), "content": None}


async def accel_check() -> dict:
    """``emulator -accel-check`` on a worker thread, bounded by ACCEL_CHECK_TIMEOUT_S."""
    return await asyncio.to_thread(_accel_check_sync)


async def diagnose_no_devices() -> dict:
    """Why ``qa_list_devices`` is empty: the AVDs on file and the acceleration verdict.

    ``{"avds": [...], "avd_error": str, "accel": {...} | None}``. Both probes run
    together under one bound of ``ACCEL_CHECK_TIMEOUT_S + 2`` s. Never raises and
    never calls ``platform_info.virtualization`` (B3).
    """
    diag: dict = {"avds": [], "avd_error": "", "accel": None}
    try:
        avds, accel = await asyncio.wait_for(
            asyncio.gather(list_avds(), accel_check()),
            timeout=ACCEL_CHECK_TIMEOUT_S + 2,
        )
    except Exception:
        logger.debug("mobile.emulator.diagnose_no_devices failed", exc_info=True)
        diag["avd_error"] = "The emulator diagnosis did not finish in time."
        return diag
    if avds.get("error"):
        diag["avd_error"] = str(avds["error"])[:300]
    else:
        diag["avds"] = [str(n) for n in (avds.get("content") or [])][:20]
    if not accel.get("error"):
        diag["accel"] = accel.get("content")
    return diag


def render_no_devices_hint(diag: dict) -> str:
    """Tester-facing paragraph for an empty device list. Empty string if nothing to say."""
    parts: list[str] = []
    avds = list((diag or {}).get("avds") or [])
    if avds:
        parts.append(
            "Emulators (AVDs) you can start: "
            + ", ".join(avds)
            + ". Call `qa_mobile_test` with `avd` set to one of them."
        )
    elif (diag or {}).get("avd_error"):
        parts.append(str(diag["avd_error"]))
    else:
        parts.append(
            "No emulator (AVD) is defined. Create one in Android Studio's Device "
            "Manager, or attach a phone with USB debugging on."
        )
    accel = (diag or {}).get("accel") or {}
    if accel.get("timed_out"):
        parts.append(
            f"`emulator -accel-check` did not answer within {ACCEL_CHECK_TIMEOUT_S} s, so "
            "hardware acceleration is undetermined. " + accel_fix("")
        )
    elif accel.get("ok") is False:
        parts.append(
            "Hardware acceleration is NOT available ("
            + str(accel.get("output") or "no detail")[:300]
            + "). Fix: "
            + str(accel.get("fix") or accel_fix(""))
        )
    return " ".join(parts)


def _exit_fix(tail: str) -> str:
    """A fix chosen from the emulator's own words; the generic one is last."""
    low = tail.lower()
    if any(k in low for k in ("whpx", "haxm", "hvf", "kvm", "hypervisor", "accel")):
        return accel_fix(tail)
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


def exit_report(
    avd: str, *, when: str = "before it appeared in `adb devices`"
) -> dict | None:
    """None while *avd*'s emulator is running or unknown; else what it said when it died.

    ``{"avd", "code", "log", "error"}``. One-shot: reporting CONSUMES the record, so
    the next start is not answered with the last failure. A zero exit with nothing
    error-like in the log is not reported (on Windows the launcher may hand off to
    a child and exit 0).
    """
    name = str(avd or "").strip()[:120]
    run = _RUNS.get(name)
    if not run:
        return None
    proc = _PROCS.get(int(run.get("pid") or 0))
    if proc is None:
        return None
    code = proc.poll()
    if code is None:
        return None
    tail = _log_tail(str(run.get("log") or ""))
    if code == 0 and not tail:
        return None
    _RUNS.pop(name, None)
    _STARTED.pop(name, None)
    return {
        "avd": name,
        "code": int(code),
        "log": str(run.get("log") or ""),
        "error": (
            f"{name}'s emulator exited with code {code} {when}. "
            f"Its own output: {wrap_untrusted('emulator log', tail, limit=600) or 'nothing was captured'}. "
            f"Fix: {_exit_fix(tail)} Full log: {run.get('log') or 'not captured'}"
        ),
    }


async def wait_boot(
    serial: str, timeout: int = 0, *, tunable: bool = True, avd: str = ""
) -> dict:
    """Poll until ``sys.boot_completed`` is ``1``.

    ``adb wait-for-device`` returns as soon as adbd answers, which is minutes
    before the launcher exists; every "the package is not installed" failure on
    a cold emulator traces back to trusting it.

    ``tunable`` says whether ``QA_MOBILE_BOOT_TIMEOUT_S`` governs the budget
    this call actually spent, and it changes the TIMEOUT MESSAGE only -- never
    the waiting. True for every caller whose budget comes from that setting:
    the default, and :func:`boot`, which slices its own remaining time off it.
    False for a caller that imposed a bounded slice of its own and hands back a
    pointer rather than waiting (:func:`session.ensure_device`, 20s). There the
    setting cannot move this wait, and a remedy naming it sends the tester to
    turn a knob that is not connected to anything they can see.

    WATCHED (S16): with *avd* named, an emulator process that exits mid-boot
    ends the wait at once in its own words (:func:`exit_report`) instead of
    after the whole budget, and every poll may send the host a progress line.
    """
    try:
        budget = int(timeout or boot_timeout_s())
        deadline = time.monotonic() + budget
        last = ""
        ticker = watch.Ticker("emulator booting")
        while time.monotonic() < deadline:
            if avd:
                gone = exit_report(avd, when="before it finished booting")
                if gone:
                    return {"error": gone["error"], "content": None}
            await ticker.tick()
            prop = await adb.getprop(serial, BOOT_PROP)
            last = (
                str(prop.get("content") or "")
                if not prop.get("error")
                else str(prop.get("error"))
            )
            if not prop.get("error") and str(prop.get("content") or "").strip() == "1":
                return {"error": None, "content": {"serial": serial, "booted": True}}
            await asyncio.sleep(POLL_INTERVAL_S)
        # The remedy, and the ONE thing that decides it: whose budget was this?
        # Both branches name Android Studio, because warming the snapshot
        # shortens the BOOT and so helps either way; only the first offers the
        # setting, because only there does raising it change what happens next.
        remedy = (
            "Raise QA_MOBILE_BOOT_TIMEOUT_S, or start the AVD from "
            "Android Studio once to warm its snapshot."
            if tunable
            else (
                "Nothing is blocked on it: QA_MOBILE_BOOT_TIMEOUT_S does not "
                "extend this slice, which is the most one tool call may spend "
                "before it answers. A cold emulator commonly needs a minute or "
                "two, so wait and ask again; start the AVD from Android Studio "
                "once to warm its snapshot if it always takes this long."
            )
        )
        return {
            "error": (
                "The emulator did not finish booting within "
                + str(budget)
                + "s (last "
                + BOOT_PROP
                + "="
                + repr(last[:60])
                + "). "
                + remedy
            ),
            "content": None,
        }
    except Exception as exc:
        logger.exception("mobile.emulator.wait_boot failed")
        return {"error": str(exc), "content": None}


async def start(avd: str, *, locale: str = "") -> dict:
    """Spawn *avd* detached and return AT ONCE. ``{"error", "content": {"pid"}}``.

    ``locale`` is keyword-only and defaults empty, so every existing caller --
    including :func:`boot`, which passes ``avd`` positionally -- is unchanged.

    Extracted from :func:`boot` (2026-09-02, Phase 3) because a caller inside an
    MCP tool call cannot afford ``boot``'s poll: it runs to
    ``QA_MOBILE_BOOT_TIMEOUT_S`` (240s by default), which is roughly four times
    a client's tool timeout, so a tester would see a dead editor rather than a
    message. ``boot`` now calls THIS, so there is exactly one place that spawns
    an emulator and the bounded and unbounded paths cannot diverge.

    THE KILL-SWITCH LIVES HERE, not on ``boot``: this is the innermost public
    function that spawns, and a guard on a caller is only as good as the list
    of callers. When this function was extracted the guard stayed on ``boot``
    and this became an unguarded spawn site in the same commit that was meant
    to close that class.
    """
    try:
        located = (sdk_locator.locate_sdk() or {}).get("content") or {}
        binary = str((located.get("tools") or {}).get("emulator") or "")
        if not binary:
            return {
                "error": (
                    "The Android emulator binary was not found, so "
                    + str(avd)
                    + " cannot be started. Install Android Studio first."
                ),
                "content": None,
            }
        paths.ensure_tree()
        command = [
            binary,
            "-avd",
            str(avd),
            "-no-snapshot-save",
            "-no-boot-anim",
        ]
        # THE ONE MECHANISM THAT WORKS UNPRIVILEGED. `persist.sys.locale` is a
        # `persist.*` property, so `adb shell setprop` needs root that the
        # `google_apis_playstore` user build does not give -- and even where the
        # write lands, the running system keeps its old configuration until it
        # restarts. Set at SPAWN, the device comes up in the language from its
        # first frame, and it survives every later reboot of that AVD.
        #
        # APPENDED, never inserted: `test_boot_spawns_detached_when_nothing_is_
        # running` reads `cmd[1:3]` by index.
        wanted = str(locale or "").strip()
        if wanted and adb.LOCALE_TAG.match(wanted):
            command += ["-prop", adb.PERSIST_LOCALE_PROP + "=" + wanted]
        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        kwargs.update(platform_info.detach_kwargs())
        log_path = _emulator_log_path(str(avd))
        if log_path:
            kwargs["log_path"] = log_path
        pid = _spawn(command, **kwargs)
        # Recorded AFTER the spawn returned, so a failed spawn is never listed
        # as booting (fix round 3, item 5).
        note_started(str(avd))
        _note_run(str(avd), pid, log_path)
        return {"error": None, "content": {"pid": pid}}
    except Exception as exc:
        logger.exception("mobile.emulator.start failed")
        return {"error": str(exc), "content": None}


async def boot(avd: str, timeout: int = 0) -> dict:
    """Ensure *avd* is running and booted, re-attaching when it already is.

    ``{"error", "content": {"serial", "avd", "reattached", "pid"}}``.

    The kill-switch is enforced in :func:`start`, which is where the spawn
    happens. It also refuses the RE-ATTACH branch, and that is deliberate for a
    reason worth stating properly: re-attach is not a terminal read-only
    operation, it is the first step of a run -- the caller goes on to poll the
    device and install the IME onto it. ``find_running``, ``stop`` and
    ``preflight`` stay unguarded so a tester can still INSPECT with the lane
    off.

    THE GUARD IS HERE, NOT ONLY IN :func:`start`, and the docstring above used
    to claim the re-attach branch was refused while the code returned it. That
    branch returns BEFORE ``start`` is ever reached, so delegating was a promise
    this function did not keep: with the lane off, a machine that happened to
    have an emulator running got a live serial back, and the caller's next moves
    are to poll it and install the IME onto it.

    An earlier note here said mutation showed a copy of the guard could be
    deleted with the suite green. That was true, and it was evidence the guard
    was UNTESTED rather than redundant -- the only test asserting it reached the
    real ``adb``, so it passed on any machine with no emulator running and
    failed on a tester's. It is hermetic now, and deleting either guard goes
    red.
    """
    try:
        ensure_adb_first_on_path()
        # A FAILED probe is not "no emulator": `or {}` collapsed the two, and
        # this function's next move is to spawn one. That is D1 exactly -- a
        # broken adb starting a second emulator over the tester's own -- and it
        # is latent here only because nothing calls boot() in production today.
        probe = await find_running(avd)
        if probe.get("error"):
            return probe
        running = probe.get("content") or {}
        if running.get("serial"):
            waited = await wait_boot(str(running["serial"]), timeout)
            if waited.get("error"):
                return waited
            return {
                "error": None,
                "content": {
                    "serial": running["serial"],
                    "avd": str(avd),
                    "reattached": True,
                    "pid": 0,
                },
            }
        started = await start(avd)
        if started.get("error"):
            return started
        pid = int((started.get("content") or {}).get("pid") or 0)
        budget = int(timeout or boot_timeout_s())

        deadline = time.monotonic() + budget
        serial = ""
        ticker = watch.Ticker("emulator starting")
        while time.monotonic() < deadline and not serial:
            await asyncio.sleep(BOOT_SERIAL_POLL_S)
            await ticker.tick()
            found = (await find_running(avd)).get("content") or {}
            serial = str(found.get("serial") or "")
            if not serial:
                # B5: a process that already died will never appear in adb.
                # Say so now, in the emulator's own words, not after the budget.
                gone = exit_report(avd)
                if gone:
                    return {"error": gone["error"], "content": None}
        if not serial:
            return {
                "error": (
                    str(avd)
                    + " was started (pid "
                    + str(pid)
                    + ") but never appeared in `adb devices` within "
                    + str(budget)
                    + "s. Start it once from Android Studio to see the "
                    "emulator's own error."
                ),
                "content": None,
            }
        waited = await wait_boot(
            serial, max(1, int(deadline - time.monotonic())), avd=avd
        )
        if waited.get("error"):
            return waited
        return {
            "error": None,
            "content": {
                "serial": serial,
                "avd": str(avd),
                "reattached": False,
                "pid": pid,
            },
        }
    except Exception as exc:
        logger.exception("mobile.emulator.boot failed")
        return {"error": str(exc), "content": None}


async def stop(serial: str) -> dict:
    """Ask the emulator to exit (``adb -s <serial> emu kill``)."""
    result = await adb.raw(["-s", str(serial), "emu", "kill"], timeout=30)
    if result.get("error"):
        return result
    return {"error": None, "content": {"serial": str(serial), "stopped": True}}


# `python_is_windows()` USED TO LIVE HERE and was deleted: a sixth spelling of
# `sys.platform == "win32"` whose only caller in the whole tree was a test
# asserting it existed. `platform_info.is_windows()` is the one producer, and
# `tests/mobile/test_mobile_platform_support.py` fails by file name if a second
# one reappears anywhere under `tools/mobile/`.
