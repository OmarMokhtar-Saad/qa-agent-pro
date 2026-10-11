"""Mobile session: device provisioning, install record and liveness. Cut verbatim from tools/mobile/session.py (P4.4)."""

from __future__ import annotations

import json
import logging
import math
import subprocess
import time
from pathlib import Path

from tools.device_manager import _valid_device_id, valid_package_name
from tools.mobile import (
    adb,
    downloader,
    emulator,
    paths,
    platform_info,
    run_store,
)
from tools.mobile.session_core import STATE_BOOTING, STATE_NO_AVD, Budget

logger = logging.getLogger("tools.mobile.session")


#: The longest this module will ever wait on the device inside one tool call.
#: Well under a client's ~50s tool timeout, and passed EXPLICITLY to
#: ``wait_boot`` -- whose own default is 240s, so an omitted keyword is the
#: defect this constant exists to prevent.
DEVICE_WAIT_BUDGET_S = 20


INSTALL_FILE = "install.json"


def _no_avd_refusal() -> dict:
    """The refusal ``ensure_device`` hands back when no AVD is named."""
    return {
        "error": (
            "No emulator (AVD) is named. Call `qa_mobile_test` again with "
            "`avd` set to one of the AVDs in Android Studio's Device "
            "Manager. `emulator=list` shows them and `emulator=create` "
            "makes one from an installed system image."
        ),
        "content": None,
    }


async def ensure_device(
    *,
    avd: str = "",
    serial: str = "",
    budget_s: int = DEVICE_WAIT_BUDGET_S,
    locale: str = "",
) -> dict:
    """Attach to a booted emulator, or start one and hand back a pointer.

    ``{"error", "content": {"state", "serial", "detail"}}`` with ``state`` one of
    ``ready`` / :data:`STATE_BOOTING`.

    ``emulator.boot`` is deliberately NOT called: it polls to
    ``QA_MOBILE_BOOT_TIMEOUT_S`` (240s by default), which is four times a
    client's tool timeout, so a tester would see a dead editor rather than a
    message. The bounded pair is ``emulator.start`` (spawn, detached, returns at
    once) plus ``emulator.wait_boot`` with an EXPLICIT budget.

    That budget is :data:`DEVICE_WAIT_BUDGET_S`, and the setting does NOT move
    it, so both waits below pass ``tunable=False``: the timeout message they
    hand to the tester must not offer a knob that cannot change this wait.
    """
    try:
        name = str(avd or "").strip()
        budget = max(1, int(budget_s or DEVICE_WAIT_BUDGET_S))
        if serial:
            # A tester-chosen (or already-adopted) device: never probe for
            # the hardcoded AVD and never spawn a second emulator under it.
            return await _wait_device_ready(serial, budget)
        if not name:
            # No house AVD to fall back on: auto-provisioning is retired
            # (docs/RETIRED_CAPABILITIES.md -> 6) and the handler resolves the
            # tester's own AVD before it gets here. Refuse rather than spawn
            # `emulator -avd ""`.
            return _no_avd_refusal()
        running = await emulator.find_running(name)
        if running.get("error"):
            return running
        serial = str((running.get("content") or {}).get("serial") or "")
        if serial:
            return await _wait_device_ready(serial, budget)
        return await _spawn_device(name, locale)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.ensure_device failed")
        return {"error": str(exc), "content": None}


async def _wait_device_ready(serial: str, budget: int) -> dict:
    """Wait for ``serial`` to boot: ``ready``, or a ``booting`` pointer on timeout."""
    waited = await emulator.wait_boot(serial, timeout=budget, tunable=False)
    if waited.get("error"):
        return {
            "error": None,
            "content": {
                "state": STATE_BOOTING,
                "serial": serial,
                # 1200, not 400: wait_boot's message may carry the
                # virtualization note, which is 295 chars on top of a
                # ~173-char timeout line. At 400 every delivery of that
                # note was cut mid-parenthesis and lost the half that
                # says WHERE to enable it -- a producer composing a
                # message no consumer delivers whole.
                "detail": str(waited["error"])[:1200],
            },
        }
    return {
        "error": None,
        "content": {"state": "ready", "serial": serial, "detail": ""},
    }


async def _spawn_device(name: str, locale: str) -> dict:
    """Start the AVD ``name`` detached and hand back a ``booting`` pointer."""
    # Only this path spawns, so only this path can set the locale from
    # the first frame. A device we ADOPTED is handled by `apply_locale`,
    # which reads back rather than assuming.
    # B5: if the emulator this server spawned last time already DIED, say so in
    # its own words instead of spawning over it and reporting "booting" again.
    gone = emulator.exit_report(name)
    if gone:
        return {"error": gone["error"], "content": None}
    started = await emulator.start(name, locale=str(locale or ""))
    if started.get("error"):
        return started
    return {
        "error": None,
        "content": {
            "state": STATE_BOOTING,
            "serial": "",
            "detail": (
                name
                + " was started (pid "
                + str((started.get("content") or {}).get("pid") or 0)
                + "). A cold emulator takes a minute or two."
            ),
        },
    }


async def device_alive(serial: str) -> dict:
    """``{alive, state}`` for one serial, answered by ``adb devices``.

    Keyed on the SERIAL rather than on ``emulator.find_running``'s AVD lookup
    deliberately: a resumed run knows the serial it was driving, and the AVD
    lookup needs a second round trip to a device that may already be gone.
    """
    try:
        wanted = str(serial or "").strip()
        if not wanted:
            return {"error": None, "content": {"alive": False, "state": "unknown"}}
        listed = await adb.devices()
        if listed.get("error"):
            return {"error": None, "content": {"alive": False, "state": "unknown"}}
        for found in listed.get("content") or []:
            if str(found) == wanted:
                return {
                    "error": None,
                    "content": {"alive": True, "state": "device"},
                }
        return {"error": None, "content": {"alive": False, "state": "absent"}}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.device_alive failed")
        return {"error": str(exc), "content": None}


async def ensure_run_device(run_id: str, *, budget: Budget | None = None) -> dict:
    """Make sure the emulator THIS RUN was driving is up, re-booting if it died.

    ``{"error", "content": {"state", "serial", "detail", "rebooted"}}`` with
    ``state`` ``ready``, :data:`STATE_BOOTING`, or :data:`STATE_NO_AVD` (plus
    ``avd``) when the AVD this run came from no longer exists.

    A resume can arrive days later, in a new chat, on a machine that has been
    rebooted since -- at which point the manifest names a serial that no longer
    exists and every adb call fails with a device error a tester cannot act on.
    So the serial is CHECKED. A re-booted emulator can also come back on a
    DIFFERENT serial, which is why the fresh one is written back to the manifest
    and why ``rebooted`` is reported: the caller must re-run the preflight
    before producing a packet against a device it has not checked.
    """
    try:
        manifest = (run_store.read_manifest(run_id) or {}).get("content")
        if not isinstance(manifest, dict) or not manifest:
            return {
                "error": (
                    "No run `"
                    + str(run_id)
                    + "` on this machine. `qa_mobile_status` with no run id "
                    "lists the runs that do exist."
                ),
                "content": None,
            }
        serial = str(manifest.get("serial") or "")
        alive = (await device_alive(serial)).get("content") or {}
        if alive.get("alive"):
            return _run_device_reply("ready", serial)
        return await _reboot_run_device(run_id, manifest, budget)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.ensure_run_device failed")
        return {"error": str(exc), "content": None}


def _run_device_reply(
    state: str, serial: str, detail: str = "", *, rebooted: bool = False
) -> dict:
    """The reply shape ``ensure_run_device`` returns."""
    return {
        "error": None,
        "content": {
            "state": state,
            "serial": serial,
            "detail": detail,
            "rebooted": rebooted,
        },
    }


async def _reboot_run_device(
    run_id: str, manifest: dict, budget: Budget | None
) -> dict:
    """Boot the run's AVD again (the serial is gone) and write back a new serial."""
    serial = str(manifest.get("serial") or "")
    avd = str(manifest.get("avd") or "")
    # The device is gone and must be booted again -- but only from an AVD
    # that still exists. Spawning a missing one "succeeds" (the launcher is
    # detached) and then times out as a boot, which tells the tester to wait
    # for something that will never come up. A failed listing (no SDK)
    # falls through to the spawn, whose own error says what is missing.
    listed = await emulator.list_avds()
    if not listed.get("error") and avd not in (listed.get("content") or []):
        reply = _run_device_reply(STATE_NO_AVD, "")
        reply["content"]["avd"] = avd
        return reply
    span = int(budget.remaining()) if budget is not None else DEVICE_WAIT_BUDGET_S
    ready = await ensure_device(
        avd=avd, budget_s=max(1, min(DEVICE_WAIT_BUDGET_S, span))
    )
    if ready.get("error"):
        return ready
    state = ready.get("content") or {}
    if str(state.get("state") or "") != "ready":
        return _run_device_reply(
            STATE_BOOTING,
            str(state.get("serial") or ""),
            str(state.get("detail") or ""),
        )
    fresh = str(state.get("serial") or "")
    if fresh and fresh != serial:
        manifest["serial"] = fresh
        run_store.write_manifest(run_id, manifest)
    return _run_device_reply("ready", fresh or serial, rebooted=True)


def _install_state_path() -> Path:
    return paths.state_file(INSTALL_FILE)


#: SECONDS an install record may be old and still support the claim "the
#: install is still running". The record lives at ONE machine-wide path with no
#: run identity, exactly like the provisioning record ``render`` ages, so past
#: this age it describes an install the reader never started. Sized off the
#: thing it bounds: an `adb install -r -g` of a large APK over a slow emulator
#: bridge is minutes, not hours, so an hour is generous for the real case and
#: still far short of the days that produced the defect. Bounded from above in
#: ``tests/mobile/test_mobile_bounds_upper.py``.
INSTALL_PENDING_MAX_AGE_S = 3600


def _started_seconds(record: dict) -> float:
    """The record's start time as a float, or ``0.0`` for anything unusable.

    This was ``float(record.get("started") or 0)`` inline. ``float()`` raises
    OverflowError on a sufficiently large int and ValueError on a non-numeric
    string, and the exception unwound into ``install_state``'s outer
    ``except Exception`` -- so one corrupt field in a machine-wide file turned
    a routine "is this app installed" answer into a FAILED CALL, which the
    caller renders as a broken device rather than as a healthy no.

    Found by strengthening the oversized-stamp test to assert the ENVELOPE
    rather than only the verdict: the aging guard beside it was catching its
    own OverflowError correctly, and this second coercion was not.
    """
    try:
        started = float(record.get("started") or 0)
    except (OverflowError, ValueError, TypeError):
        return 0.0
    # A NON-FINITE VALUE DOES NOT RAISE, so the except above never sees it.
    # `json.loads` accepts bare NaN, Infinity and -Infinity, and this record is
    # a machine-wide file this process did not write. Every comparison against
    # NaN is False, so a NaN returned here walks through the freshness checks
    # downstream instead of tripping them -- the same shape that let a NaN
    # timestamp make the run collector delete a live run. `or 0` is a default
    # for a FALSY value and NaN is truthy, so it is not a guard either.
    if not math.isfinite(started):
        return 0.0
    return started


def _install_is_recent(record: dict, now: float) -> bool:
    """Did the install this record describes start recently enough to still run?

    Same rule, same reason, as ``render._record_is_current``: a machine-wide
    record with no identity is only as good as its age, so a MISSING or
    non-numeric ``started`` is not recent -- an undated record cannot support a
    claim about now -- and neither is a stamp in the future.
    """
    stamp = record.get("started")
    if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
        return False
    try:
        age = float(now) - float(stamp)
    except (OverflowError, ValueError):
        return False
    return 0 <= age <= INSTALL_PENDING_MAX_AGE_S


def _read_install_record() -> dict:
    """The last started install, or ``{}``.

    Unreadable is deliberately not an error: the DEVICE is the authority on
    whether the package is present, and this record only says what was started.
    """
    try:
        target = _install_state_path()
        if not target.is_file():
            return {}
        body = json.loads(target.read_text(encoding="utf-8"))
        return body if isinstance(body, dict) else {}
    except (OSError, ValueError):
        logger.info("mobile.session: unreadable install state")
        return {}


def start_install(serial: str, apk_path: str, package: str) -> dict:
    """Spawn ``adb install`` DETACHED and record what was started.

    ``adb.install`` is bounded at 300s, which no tool call may hold, so the
    install outlives this call by design. Completion is NOT inferred from the
    process: :func:`install_state` asks the device whether the package is
    present, because a pid that exited proves nothing and an install that failed
    must never read as success.

    Reads NO switch: the mobile lane ships on with no flag. The handler that
    normally calls this checks the lane predicate and ``apply``, and those are
    guards on that CALLER only, while this function spawns a detached install
    on the tester's device -- an effect that outlives the call. A new caller
    has to bring its own consent; nothing here asks for it.
    """
    try:
        path = Path(str(apk_path or "")).expanduser()
        refusal = _install_refusal(serial, package, path)
        if refusal:
            return refusal
        paths.ensure_tree()
        proc = _spawn_install(serial, path)
        payload = {
            "package": str(package),
            "serial": str(serial),
            "apk": str(path),
            "pid": int(getattr(proc, "pid", 0) or 0),
            "started": time.time(),
        }
        downloader.write_progress(_install_state_path(), payload)
        return {"error": None, "content": payload}
    except Exception as exc:
        logger.exception("mobile.session.start_install failed")
        return {"error": str(exc), "content": None}


def _install_refusal(serial: str, package: str, path: Path) -> dict | None:
    """The error reply for an install that must not start, else None."""
    if not valid_package_name(package):
        return {
            "error": "Refusing " + repr(str(package)[:60]) + " as a package name.",
            "content": None,
        }
    if not _valid_device_id(serial):
        return {
            "error": "Refusing " + repr(str(serial)[:40]) + " as a device id.",
            "content": None,
        }
    if path.suffix.lower() != ".apk" or not path.is_file():
        return {
            "error": (
                "No .apk file at "
                + str(path)[:200]
                + ". Give the full path to the file, and nothing is installed."
            ),
            "content": None,
        }
    problem = adb.apk_problem(path)
    if problem:
        return {
            "error": (
                "Not installing "
                + str(path)[:200]
                + ": "
                + problem
                + ". Nothing was installed."
            ),
            "content": None,
        }
    return None


def _spawn_install(serial: str, path: Path) -> object:
    """Start ``adb install -r -g`` detached and return the process."""
    command = [adb.resolve_adb(), "-s", str(serial), "install", "-r", "-g", str(path)]
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    kwargs.update(platform_info.detach_kwargs())
    return subprocess.Popen(command, **kwargs)  # noqa: S603 - argv, no shell


async def install_state(serial: str, package: str) -> dict:
    """``{probed, installed, pending, apk, started}`` -- answered by the DEVICE.

    ``probed`` IS THE POINT. Whether the device answered is a fact only this
    function can know, so it SAYS so rather than leaving callers to infer it
    from the envelope. They cannot: this function's own ``error`` is set only
    in the defensive branch below, so on the failure it exists to catch it is
    ``None`` and ``probed = not state.get("error")`` reads True. A caller then

    ``pending`` ASSERTS EXACTLY WHAT THE RECORD ESTABLISHED, no wider. The
    record is one machine-wide file naming a package, a serial and a start
    time, so "the install is still running" needs all three to line up: the
    same package, the SAME DEVICE, and a start recent enough that the install
    could still be in flight. It used to need only the package name, so a
    five-day-old record from a different emulator still told the tester an
    install was in progress -- the same stale-machine-wide-record defect this
    lane fixed for the provisioning record, in the commit that fixed it.
    A record from another device is wrong at any age, which is why the serial
    is matched as well as the age.
    printed "`pkg` is not installed on the emulator" off a probe that never
    answered (2026-09-08, round 2).

    ONE PIECE OF EVIDENCE, and it is exact: ``adb.installed_packages`` reports
    an ``error`` when adb could not be run at all AND when the command ran and
    failed (a non-zero exit -- surfaced there, in the one producer of this
    list, rather than guessed at here from an empty result). So the whole rule
    is the envelope of that call, and there is no second, weaker derivation of
    it in this module.

    ``installed`` and ``pending`` are POSITIVE claims, made only from an
    answered probe: an unprobed call returns both False, so no caller can turn
    "we do not know" into "the install is still running".
    """
    try:
        record = _read_install_record()
        listed = await adb.installed_packages(serial)
        probed = not listed.get("error")
        names = list(listed.get("content") or [])
        content = _install_state_content(record, probed, names, serial, package)
        return {"error": None, "content": content}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.install_state failed")
        return {"error": str(exc), "content": None}


def _install_state_content(
    record: dict, probed: bool, names: list, serial: str, package: str
) -> dict:
    """The ``install_state`` answer, derived from one probe and the record."""
    installed = bool(probed and str(package) in names)
    pending = bool(
        probed
        and not installed
        and record.get("package") == str(package)
        and str(record.get("serial") or "") == str(serial)
        and _install_is_recent(record, time.time())
    )
    # FUZZY MATCH, FROM THE SAME PROBE. `installed_packages` already listed
    # every package id on the device to answer `installed` above -- this
    # reuses that ONE round trip rather than spending a second adb call to
    # answer "did the caller guess the wrong id". A tester (or the model
    # on their behalf) names an app, not a bundle id, and a wrong guess
    # used to dead-end in "not installed" with no way forward short of a
    # manual `adb shell pm list packages`. The cap of 5 is a LOCAL bound
    # (not a module constant): a hint in an error message, not a listing,
    # so it does not need the CEILINGS governance a shared cap would.
    suggestions: list[str] = []
    if probed and not installed and names:
        import difflib

        suggestions = difflib.get_close_matches(str(package), names, n=5, cutoff=0.4)
    return {
        "probed": bool(probed),
        "installed": installed,
        "pending": pending,
        "apk": str(record.get("apk") or ""),
        "started": _started_seconds(record),
        "suggestions": suggestions,
    }
