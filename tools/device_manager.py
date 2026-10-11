"""Discover attached mobile devices and capture screenshots (opt-in feature).

Supports:
  * Android via ``adb``                (real devices + emulators)
  * iOS Simulators via ``xcrun simctl``
  * iOS physical devices via ``xcrun devicectl`` (Xcode 15+), with
    ``idevicescreenshot`` (libimobiledevice) as a screenshot fallback.

House rules (mirrors tools/jira_fetcher.py's never-raise contract):
  * Never raises to callers -- every public function returns a dict carrying an
    ``"error"`` key (``None`` on success).
  * All subprocesses run via ``asyncio.create_subprocess_exec`` with an explicit
    ARGUMENT LIST -- never a shell string, never ``shell=True`` -- so a
    tester-influenced value can never be interpreted by a shell. Device ids are
    additionally whitelist-validated before use, and every call is bounded by a
    timeout.
  * A missing CLI tool (adb / xcrun / devicectl / idevicescreenshot not
    installed) degrades to a friendly message instead of crashing.

Device dicts use the fields: ``id``, ``name``, ``platform`` (``android``/``ios``),
``kind`` (``device``/``emulator``/``simulator``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time

from config.settings import settings
from tools.device_ids import (  # noqa: F401  re-exports: moved names keep their old path
    _ADB_KNOWN_STATES,
    _ANDROID_SYSTEM_PREFIXES,
    _DEVICE_ID_RE,
    _MAX_ADB_LINES,
    _MAX_ADB_STATE_CHARS,
    _MAX_PACKAGE_LEN,
    _PACKAGE_NAME_RE,
    SYSTEM_PACKAGE_PREFIXES,
    AdbRows,
    _adb_candidate_lines,
    _adb_row,
    _valid_device_id,
    _valid_package_name,
    parse_adb_devices,
    parse_pm_packages,
    valid_package_name,
)
from tools.process_runner import arun
from tools.secure_temp import make_secure_temp_path

logger = logging.getLogger(__name__)


# UDID shapes seen in `xcrun devicectl list devices` output.
_UDID_RE = re.compile(
    r"\b([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
    r"|[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}"
    r"|[0-9A-Fa-f]{25})\b"
)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _cmd_timeout() -> int:
    return settings.qa_device_command_timeout


def _shot_timeout() -> int:
    return settings.qa_device_screenshot_timeout


def _adb_binary() -> str:
    """The adb every spawn here uses: the SDK's own, as the mobile lane resolves it.

    A bare ``"adb"`` depends on this process's PATH, which an IDE-spawned
    server often lacks, so qa_list_devices reported "No devices detected"
    for a device qa_mobile_test was driving. Deferred import: keeps this call
    working when the mobile modules are absent. A build without the mobile modules
    degrades to the bare ``"adb"`` on PATH instead of raising.
    """
    try:
        from tools.mobile import adb as mobile_adb
    except ImportError:
        return "adb"
    return mobile_adb.resolve_adb()


#: Above this, a single subprocess call here is worth a WARNing rather than a
#: DEBUG line -- mirrors ``tools.mobile.adb.SLOW_CALL_CEILING_S``, kept as its
#: own constant because this module's calls are unauthenticated listing/probe
#: commands, not the mobile lane's per-action calls (audit item 1).
SLOW_CALL_CEILING_S = 5.0

#: Hard cap on the quiet ``adb start-server`` that runs before ``adb devices -l``
#: (Batch 1b, B4). A wedged adb daemon used to surface only as a silent empty
#: device list after the full command timeout; now the tester is told.
ADB_START_SERVER_TIMEOUT_S = 8.0


async def _run(cmd: list[str], timeout: int) -> tuple[int, bytes, bytes]:
    """Run *cmd* (argument list, NO shell) and return (returncode, stdout, stderr).

    Raises ``FileNotFoundError`` when the binary is missing (callers convert this
    to a friendly error) and ``asyncio.TimeoutError`` when the call overruns
    *timeout*.
    """
    # ``started`` is taken BEFORE the spawn so a slow exec counts toward the
    # duration line, and stdin is DEVNULL so a child that reads stdin cannot hang
    # on the server's own (MCP stdio) pipe.
    started = time.monotonic()

    def _drain_failed(_exc: Exception) -> None:
        # The kill is what matters; draining after it is courtesy. Logged
        # rather than passed so a device that cannot be reaped leaves a
        # trace -- the warning below only says the command timed out.
        logger.debug("device_manager: drain after kill failed", exc_info=True)

    r = await arun(
        cmd,
        timeout=timeout,
        stdin=asyncio.subprocess.DEVNULL,
        communicate_input=False,
        kill_drain=True,
        kill_on_cancel=True,
        ignore_kill_error=False,
        on_drain_error=_drain_failed,
    )
    if r.timed_out:
        logger.warning(
            "device_manager: command timed out after %ss: %s", timeout, " ".join(cmd)
        )
    if r.exc is not None:
        raise r.exc
    duration = time.monotonic() - started
    if duration > SLOW_CALL_CEILING_S:
        logger.warning(
            "device_manager: call took %.2fs (over %ss): %s",
            duration,
            SLOW_CALL_CEILING_S,
            " ".join(cmd),
        )
    else:
        logger.debug("device_manager: call took %.2fs: %s", duration, " ".join(cmd))
    return r.rc or 0, r.stdout or b"", r.stderr or b""


# --------------------------------------------------------------------------- #
# Listing
# --------------------------------------------------------------------------- #


#: Written for a tester who does not write code: what to DO, on the phone, now.
_ADB_REMEDIATION = {
    "unauthorized": (
        "Unlock the phone's screen. It is asking 'Allow USB debugging?' -- tick "
        "'Always allow from this computer' and tap Allow. If no dialog appears, "
        "unplug the cable and plug it back in while the screen is unlocked."
    ),
    "authorizing": (
        "The phone is still deciding whether to trust this computer. Unlock its "
        "screen, answer the 'Allow USB debugging?' dialog, then run "
        "`qa_list_devices` again."
    ),
    "offline": (
        "The connection dropped. Unplug the cable and re-plug it -- a different "
        "USB port or cable often fixes this -- then run `qa_list_devices` again."
    ),
    "connecting": (
        "This computer is still connecting to the device. Wait a few seconds and "
        "run `qa_list_devices` again."
    ),
    "no_permissions": (
        "This computer is not allowed to talk to the device. On Linux the udev "
        "rules are missing; on Windows it is the USB driver. Ask whoever "
        "supports your machine for the Android USB rules/driver for this phone, "
        "then re-plug it."
    ),
}

_ADB_REMEDIATION_UNKNOWN = (
    "This computer can see the device, but adb reports a state we do not "
    "recognise, so we cannot say what it needs. Unlock the phone, re-plug the "
    "cable and run `qa_list_devices` again; if it stays this way, send this "
    "whole message to whoever supports your machine."
)


def adb_state_remediation(state: object, raw_state: object = "") -> str:
    """The tester-facing next step for an adb state. ``""`` for ``device``.

    ONE producer for this text: the renderer must not invent a second wording,
    and a state with no entry gets the unknown text rather than silence. It is
    OUR prose, which is why the renderer keeps it OUTSIDE the untrusted block.

    ``raw_state`` is accepted and DELIBERATELY UNUSED. Selection keys off the
    bounded ``_ADB_KNOWN_STATES`` vocabulary alone, so a hostile device can
    choose WHICH canned sentence it sees but cannot write one. Wiring
    ``raw_state`` into the lookup or into the returned text would put
    device-supplied bytes inside the trusted region -- the untrusted-content
    finding this design already answered once. Do not resolve the unused
    argument by using it.
    """
    key = str(state or "")
    if key == "device":
        return ""
    return _ADB_REMEDIATION.get(key, _ADB_REMEDIATION_UNKNOWN)


def _android_row(parsed: dict) -> dict:
    """One ``parse_adb_devices`` row as a device dict for the listing API.

    EVERY row gains a ``state`` key, usable ones included (always ``device``).
    That is ADDITIVE-SAFE, not byte-identical to the old shape: every consumer
    of this list reads named fields through ``.get()`` and none iterates the
    keys or compares whole dicts, so nothing is disturbed -- but the row is not
    the same object it was, and a comment claiming otherwise would be a lie the
    next reader trusts.
    """
    serial = str(parsed.get("serial") or "")
    model = str((parsed.get("metadata") or {}).get("model") or "")
    name = (model.replace("_", " ") or serial) if model else serial
    kind = "emulator" if serial.startswith("emulator-") else "device"
    row = {
        "id": serial,
        "name": name,
        "platform": "android",
        "kind": kind,
        "state": str(parsed.get("state") or "unknown"),
    }
    if not parsed.get("usable"):
        row["raw_state"] = str(parsed.get("raw_state") or "")
        row["remediation"] = adb_state_remediation(
            parsed.get("state"), parsed.get("raw_state")
        )
    return row


class _AndroidListing(tuple):
    """``(usable, unusable, dropped_lines)`` plus a ``.problem`` sentence.

    A tuple subclass so every existing consumer that unpacks three values keeps
    working; ``problem`` is None when adb answered cleanly, else one sentence
    saying what went wrong and how to fix it (B4).
    """

    problem: str | None

    def __new__(cls, usable, unusable, dropped, problem=None):
        self = super().__new__(cls, (usable, unusable, dropped))
        self.problem = problem
        return self


async def _start_adb_server() -> str | None:
    """Bounded, quiet ``adb start-server``. None when fine, else a sentence.

    EVERY stdio handle is DEVNULL on purpose: the daemon it forks inherits them,
    and a daemon holding our pipe makes ``communicate()`` wait for the daemon to
    exit, which it never does. A missing adb is not a problem here (the listing
    step reports that itself). Never raises.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            _adb_binary(),
            "start-server",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return None
    try:
        await asyncio.wait_for(proc.communicate(), timeout=ADB_START_SERVER_TIMEOUT_S)
    except asyncio.CancelledError:
        # A caller's own deadline cancelled us: do not leave the child behind.
        try:
            proc.kill()
        except OSError:
            logger.debug(
                "device_manager: kill after start-server cancel failed", exc_info=True
            )
        raise
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            logger.debug(
                "device_manager: kill after start-server timeout failed", exc_info=True
            )
        logger.warning(
            "device_manager: adb start-server timed out after %ss",
            ADB_START_SERVER_TIMEOUT_S,
        )
        return (
            f"`adb start-server` did not finish within {ADB_START_SERVER_TIMEOUT_S:.0f} s. "
            "Fix: run `adb kill-server` then `adb start-server` in a terminal; if it "
            "still hangs, another adb (Android Studio, a phone-mirroring tool) may "
            "hold port 5037 -- close it and retry."
        )
    except Exception:
        logger.debug("device_manager: adb start-server probe failed", exc_info=True)
    return None


async def _list_android_all() -> tuple[list[dict], list[dict], int]:
    """``(usable, unusable)`` from ``adb devices -l``. ``([], [])`` if adb missing.

    The UNUSABLE list is the point: a phone whose USB-debugging prompt has not
    been accepted is physically attached and must be reported, with what to do
    about it, rather than dropped. A serial that fails the id whitelist is
    reported here too -- it is rendered (inside an untrusted block) and never
    passed to a subprocess.
    """
    try:
        problem = await _start_adb_server()
        rc, out, err = await _run([_adb_binary(), "devices", "-l"], _cmd_timeout())
    except FileNotFoundError:
        logger.info("device_manager: adb not installed -- skipping Android devices")
        return _AndroidListing([], [], 0, None)
    except asyncio.TimeoutError:
        # Was a silent ([], [], 0): the tester saw "no devices" for a wedged adb.
        return _AndroidListing(
            [],
            [],
            0,
            problem
            or (
                f"`adb devices -l` did not answer within {_cmd_timeout()} s. "
                "Fix: run `adb kill-server` then `adb start-server`, then retry."
            ),
        )
    if rc != 0:
        detail = err.decode(errors="replace")[:200]
        logger.warning("device_manager: adb devices failed: %s", detail)
        return _AndroidListing([], [], 0, f"`adb devices -l` failed: {detail}")
    usable: list[dict] = []
    unusable: list[dict] = []
    rows = parse_adb_devices(out.decode(errors="replace"))
    for parsed in rows:
        (usable if parsed.get("usable") else unusable).append(_android_row(parsed))
    # THE THIRD ELEMENT IS THE POINT: a list that is missing transports must
    # not be reported as the whole truth. `list_devices` carries it to the
    # renderer, which tells the tester in our own prose.
    return _AndroidListing(usable, unusable, rows.dropped_lines, problem)


async def _list_android() -> list[dict]:
    """Devices/emulators ``adb`` can actually drive. Empty list if adb missing.

    A thin filter over ``_list_android_all`` -- kept so callers that only ever
    wanted drivable devices keep their exact contract.
    """
    # The dropped-line count is DELIBERATELY discarded here: this function's
    # whole contract is "drivable devices, exactly the old shape", it has no
    # production caller, and the reporting path is `list_devices`, which does
    # carry it. Widening this signature would move the disclosure away from the
    # one place a person reads.
    usable, _unusable, _dropped = await _list_android_all()
    return usable


#: TTL for the whole-device-listing result (audit item 5: discovery forked
#: xcrun on every call). Long enough to span a menu-then-run exchange on a
#: slow Windows host (Cursor/Windows audit S5); a device plugged in mid-run is
#: still found at once by ``qa_list_devices``, which always passes
#: ``refresh=True``, and a device that drops off drops the cache
#: (``invalidate_discovery_cache``).
DISCOVERY_CACHE_TTL_S = 60.0

#: ``(cached_at, result)`` from the last successful ``list_devices`` probe, or
#: ``None`` before the first call. Process-local; ``tests/conftest.py`` resets
#: it between tests.
_discovery_cache: tuple[float, dict] | None = None


def invalidate_discovery_cache() -> None:
    """Forget the cached listing, so the next ``list_devices`` re-probes.

    Called when adb reports that a device went away; a cached listing that
    still shows it would otherwise be served for up to the whole TTL.
    """
    global _discovery_cache
    _discovery_cache = None


# Subscribe at import time so a device-gone seen before the first
# ``_adb_binary`` call still drops the cache. ImportError covers a dist
# without the mobile lane.
try:
    from tools.mobile import adb as _mobile_adb
except ImportError as _exc:
    # A dist without the mobile lane is normal; any other missing module is not.
    if not (_exc.name or "").startswith("tools.mobile"):
        logger.debug("device-gone subscription skipped: %s", _exc)
else:
    _mobile_adb.on_device_gone(invalidate_discovery_cache)


#: Whether full Xcode (not just the Command Line Tools) is installed, detected
#: once per process and cached -- mirrors ``host_privileges.probe``'s shape.
#: ``None`` before the first probe.
_XCODE_CACHE: bool | None = None


async def _xcode_present(refresh: bool = False) -> bool:
    """True when ``xcrun -f simctl`` resolves, i.e. full Xcode is installed.

    The Command Line Tools alone answer "unable to find utility simctl", which
    is the noisy failure audit item 5 reported forking on every discovery
    call. Cached per process; ``refresh=True`` re-probes.
    """
    global _XCODE_CACHE
    if _XCODE_CACHE is not None and not refresh:
        return _XCODE_CACHE
    try:
        rc, _out, _err = await _run(["xcrun", "-f", "simctl"], _cmd_timeout())
        _XCODE_CACHE = rc == 0
    except (FileNotFoundError, asyncio.TimeoutError):
        _XCODE_CACHE = False
    return _XCODE_CACHE


async def _list_ios_simulators() -> list[dict]:
    """Booted iOS simulators via ``xcrun simctl list devices booted -j``."""
    if not await _xcode_present():
        return []
    try:
        rc, out, err = await _run(
            ["xcrun", "simctl", "list", "devices", "booted", "-j"], _cmd_timeout()
        )
    except FileNotFoundError:
        logger.info("device_manager: xcrun not installed -- skipping iOS simulators")
        return []
    except asyncio.TimeoutError:
        return []
    if rc != 0:
        logger.warning(
            "device_manager: simctl list failed: %s",
            err.decode(errors="replace")[:200],
        )
        return []
    try:
        data = json.loads(out.decode(errors="replace") or "{}")
    except json.JSONDecodeError:
        logger.warning("device_manager: could not parse simctl JSON output")
        return []
    devices: list[dict] = []
    for runtime_devices in (data.get("devices") or {}).values():
        for sim in runtime_devices or []:
            if (sim.get("state") or "").lower() != "booted":
                continue
            udid = sim.get("udid")
            if not _valid_device_id(udid):
                continue
            devices.append(
                {
                    "id": udid,
                    "name": sim.get("name") or udid,
                    "platform": "ios",
                    "kind": "simulator",
                }
            )
    return devices


async def _list_ios_physical() -> list[dict]:
    """Physical iOS devices via ``xcrun devicectl list devices`` (Xcode 15+).

    devicectl's tabular text output is parsed leniently: any UDID-shaped token on
    a data row is treated as the identifier and the preceding text as the name.
    Best-effort -- returns an empty list if devicectl is unavailable or the
    format is unrecognised.
    """
    if not await _xcode_present():
        return []
    try:
        rc, out, err = await _run(
            ["xcrun", "devicectl", "list", "devices"], _cmd_timeout()
        )
    except FileNotFoundError:
        logger.info(
            "device_manager: xcrun/devicectl not installed -- skipping physical iOS"
        )
        return []
    except asyncio.TimeoutError:
        return []
    if rc != 0:
        logger.info(
            "device_manager: devicectl list unavailable: %s",
            err.decode(errors="replace")[:200],
        )
        return []
    devices: list[dict] = []
    for raw in out.decode(errors="replace").splitlines():
        line = raw.strip()
        low = line.lower()
        if not line or low.startswith(("devices", "name", "---", "identifier")):
            continue
        match = _UDID_RE.search(line)
        if not match:
            continue
        udid = match.group(1)
        if not _valid_device_id(udid):
            continue
        name = line[: match.start()].strip() or udid
        devices.append({"id": udid, "name": name, "platform": "ios", "kind": "device"})
    return devices


async def list_devices(refresh: bool = False) -> dict:
    """Return every attached Android/iOS device, emulator, and simulator.

    Shape: ``{"content": [{id, name, platform, kind, state}, ...], "unusable":
    [{..., state, raw_state, remediation}, ...], "error": None}``.

    ``content`` MEANS exactly what it always did -- devices this server can
    drive -- and each Android row is additively extended with ``state`` (always
    ``device`` there). ``unusable`` is the new sibling: Android transports that
    are attached but not drivable yet (``unauthorized``, ``offline``, ``no
    permissions``, an unrecognised state). It is informational ONLY and must
    never be offered as a choice; its serial and raw state are device-supplied
    text, so any renderer must wrap them via ``tools.untrusted``. The empty list
    is always present, including on the error path, so no reader has to
    special-case its absence.
    Never raises. A per-platform tool being missing is NOT an error -- that
    platform is simply skipped. Only a truly unexpected failure sets ``error``.

    Cached for ``DISCOVERY_CACHE_TTL_S`` seconds (audit item 5): discovery
    forked ``xcrun simctl``/``devicectl`` on every call, which is slow and,
    without full Xcode, fails outright. ``refresh=True`` bypasses the cache.
    """
    now = time.monotonic()
    if not refresh and _discovery_cache is not None:
        cached_at, cached_result = _discovery_cache
        if (now - cached_at) < DISCOVERY_CACHE_TTL_S:
            return dict(cached_result)
    try:
        return await _list_devices_discover(now)
    except Exception as exc:
        logger.exception("device_manager: unexpected error listing devices")
        return {
            "error": str(exc),
            "content": None,
            "unusable": [],
            "adb_dropped_lines": 0,
            "adb_problem": None,
        }


async def _list_devices_discover(now: float) -> dict:
    """Run every platform's discovery, build the reply and cache it when clean."""
    global _discovery_cache
    android, simulators, physical = await asyncio.gather(
        _list_android_all(),
        _list_ios_simulators(),
        _list_ios_physical(),
    )
    usable_android, unusable_android, dropped_lines = android
    # Plain 3-tuples (tests, older callers) carry no problem.
    adb_problem = getattr(android, "problem", None)
    devices = [*usable_android, *simulators, *physical]
    logger.info(
        "device_manager: discovered %d device(s), %d attached but unusable",
        len(devices),
        len(unusable_android),
    )
    result = {
        "content": devices,
        "unusable": unusable_android,
        # Lines of `adb devices` output the parser did not read. Always
        # present, including on the error path below, so no reader has to
        # special-case its absence -- the same rule `unusable` follows.
        "adb_dropped_lines": dropped_lines,
        # One sentence when `adb start-server` / `adb devices -l` timed out or
        # failed (B4); None otherwise. Always present, like `unusable`.
        "adb_problem": adb_problem,
        "error": None,
    }
    # A failed adb probe is not a result: cached, it would keep telling the
    # tester about a problem they have already fixed.
    if adb_problem is None:
        _discovery_cache = (now, dict(result))
    return result


# --------------------------------------------------------------------------- #
# Installed-app listing (device-driven app picker for the exploratory wizard)
# --------------------------------------------------------------------------- #


# CFBundleIdentifier occurrences in `xcrun simctl listapps` plist-ish output.
_CFBUNDLE_ID_RE = re.compile(r'CFBundleIdentifier\s*=\s*"([^"]+)"')


def _parse_pm_list(out: bytes, *, drop_system: bool = False) -> list[dict]:
    """Parse ``pm list packages`` output ('package:<id>' per line) into app dicts.

    Every id is whitelist-validated before inclusion; invalid ids (and, when
    *drop_system*, obvious system namespaces) are skipped. Duplicates collapse."""
    return [
        {"id": p, "name": p}
        for p in parse_pm_packages(
            out.decode(errors="replace"), drop_system=drop_system
        )
    ]


async def _list_android_apps(device_id: str) -> list[dict]:
    """Third-party packages via ``adb -s <id> shell pm list packages -3``.

    Some emulators report an empty third-party list; in that case fall back to
    the full ``pm list packages`` and drop obvious system namespaces. A missing
    adb, a timeout, or a non-zero return code all degrade to an empty list."""
    try:
        rc, out, _err = await _run(
            [_adb_binary(), "-s", device_id, "shell", "pm", "list", "packages", "-3"],
            _cmd_timeout(),
        )
    except FileNotFoundError:
        logger.info("device_manager: adb not installed -- cannot list Android apps")
        return []
    except asyncio.TimeoutError:
        return []
    if rc != 0:
        logger.warning("device_manager: adb 'pm list packages -3' failed (rc=%s)", rc)
        return []
    apps = _parse_pm_list(out)
    if apps:
        return apps
    # Fallback: full package list minus obvious system namespaces.
    try:
        rc, out, _err = await _run(
            [_adb_binary(), "-s", device_id, "shell", "pm", "list", "packages"],
            _cmd_timeout(),
        )
    except (FileNotFoundError, asyncio.TimeoutError):
        return []
    if rc != 0:
        return []
    return _parse_pm_list(out, drop_system=True)


async def _list_ios_simulator_apps(device_id: str) -> list[dict]:
    """Installed apps on a booted simulator via ``xcrun simctl listapps <udid>``.

    Best-effort: every CFBundleIdentifier occurrence is collected and
    whitelist-validated. A parse failure yields an empty list, never an error."""
    try:
        rc, out, _err = await _run(
            ["xcrun", "simctl", "listapps", device_id], _cmd_timeout()
        )
    except FileNotFoundError:
        logger.info("device_manager: xcrun not installed -- cannot list iOS apps")
        return []
    except asyncio.TimeoutError:
        return []
    if rc != 0:
        logger.info("device_manager: simctl listapps failed (rc=%s)", rc)
        return []
    apps: list[dict] = []
    seen: set[str] = set()
    for match in _CFBUNDLE_ID_RE.finditer(out.decode(errors="replace")):
        pkg = match.group(1)
        if pkg in seen or not _valid_package_name(pkg):
            continue
        seen.add(pkg)
        apps.append({"id": pkg, "name": pkg})
    return apps


async def list_installed_apps(device: dict) -> dict:
    """Return the apps installed on *device* for the exploratory app picker.

    Shape: ``{"content": [{"id": ..., "name": ...}, ...], "error": None}``.
    Never raises. Android and iOS *simulators* are supported; physical iOS is
    unsupported for now and returns an empty list (NOT an error). A missing CLI
    tool, timeout, or unreadable output all degrade to an empty list so the
    caller can fall back to a manual prompt. The device id is
    whitelist-validated before it reaches any subprocess."""
    device = device or {}
    device_id = device.get("id")
    platform = (device.get("platform") or "").lower()
    kind = (device.get("kind") or "").lower()

    if not _valid_device_id(device_id):
        return {"error": "invalid or missing device id", "content": None}

    try:
        if platform == "android":
            apps = await _list_android_apps(device_id)
        elif platform == "ios" and kind == "simulator":
            apps = await _list_ios_simulator_apps(device_id)
        elif platform == "ios":
            # Physical iOS app enumeration is unsupported for now.
            apps = []
        else:
            return {
                "error": f"unsupported device platform: {platform or 'unknown'}",
                "content": None,
            }
        logger.info(
            "device_manager: listed %d installed app(s) on %s", len(apps), platform
        )
        return {"content": apps, "error": None}
    except Exception as exc:
        logger.exception("device_manager: unexpected error listing installed apps")
        return {"error": str(exc), "content": None}


# --------------------------------------------------------------------------- #
# Screenshot capture
# --------------------------------------------------------------------------- #


def _png_result(data: bytes) -> dict:
    """Validate PNG magic bytes and wrap into the success dict, else an error."""
    if not data or not data.startswith(_PNG_MAGIC):
        return {"error": "capture did not return a valid PNG image", "content": None}
    return {"content": data, "media_type": "image/png", "error": None}


async def _screenshot_android(device_id: str) -> dict:
    rc, out, err = await _run(
        [_adb_binary(), "-s", device_id, "exec-out", "screencap", "-p"], _shot_timeout()
    )
    if rc != 0:
        return {
            "error": err.decode(errors="replace")[:200] or "adb screencap failed",
            "content": None,
        }
    return _png_result(out)


async def _screenshot_ios_simulator(device_id: str) -> dict:
    """Simulator screenshot via a secure temp file.

    Older Xcode supported streaming with the documented ``-`` (stdout) target,
    but newer versions treat ``-`` as a literal filename — writing a stray
    ``./-`` file and nothing to stdout — so the only portable target is a real
    file path (same pattern as the physical-device capture).
    """
    path = make_secure_temp_path(prefix="qa_sim_shot_", suffix=".png")
    try:
        rc, _out, err = await _run(
            ["xcrun", "simctl", "io", device_id, "screenshot", path], _shot_timeout()
        )
        if rc == 0 and os.path.exists(path) and os.path.getsize(path) > 0:
            with open(path, "rb") as handle:
                return _png_result(handle.read())
        return {
            "error": err.decode(errors="replace")[:200] or "simctl screenshot failed",
            "content": None,
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


async def _screenshot_ios_physical(device_id: str) -> dict:
    """Physical iOS screenshot: devicectl to a temp file, idevicescreenshot fallback.

    devicectl and idevicescreenshot both write to a file rather than stdout, so
    the shot is written to a secure temp path, read back, and unlinked.
    """
    path = make_secure_temp_path(prefix="qa_device_shot_", suffix=".png")
    try:
        try:
            rc, _out, err = await _run(
                [
                    "xcrun",
                    "devicectl",
                    "device",
                    "screenshot",
                    "--device",
                    device_id,
                    path,
                ],
                _shot_timeout(),
            )
            if rc == 0 and os.path.exists(path) and os.path.getsize(path) > 0:
                with open(path, "rb") as handle:
                    return _png_result(handle.read())
            logger.info(
                "device_manager: devicectl screenshot failed -- trying idevicescreenshot"
            )
        except FileNotFoundError:
            logger.info("device_manager: devicectl missing -- trying idevicescreenshot")

        rc, _out, err = await _run(
            ["idevicescreenshot", "-u", device_id, path], _shot_timeout()
        )
        if rc == 0 and os.path.exists(path) and os.path.getsize(path) > 0:
            with open(path, "rb") as handle:
                return _png_result(handle.read())
        return {
            "error": err.decode(errors="replace")[:200]
            or "physical iOS screenshot failed",
            "content": None,
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


async def capture_screenshot(device: dict) -> dict:
    """Capture a PNG screenshot from *device*.

    Shape: ``{"content": <png bytes>, "media_type": "image/png", "error": None}``
    on success, else ``{"error": str, "content": None}``. Never raises. The device
    id is whitelist-validated before it is passed to any subprocess.
    """
    device = device or {}
    device_id = device.get("id")
    platform = (device.get("platform") or "").lower()
    kind = (device.get("kind") or "").lower()

    if not _valid_device_id(device_id):
        return {"error": "invalid or missing device id", "content": None}

    try:
        if platform == "android":
            return await _screenshot_android(device_id)
        if platform == "ios" and kind == "simulator":
            return await _screenshot_ios_simulator(device_id)
        if platform == "ios":
            return await _screenshot_ios_physical(device_id)
        return {
            "error": f"unsupported device platform: {platform or 'unknown'}",
            "content": None,
        }
    except FileNotFoundError as exc:
        logger.info("device_manager: capture tool missing: %s", exc)
        return {
            "error": "the required capture tool is not installed on this machine",
            "content": None,
        }
    except asyncio.TimeoutError:
        return {"error": "the screenshot capture timed out", "content": None}
    except Exception as exc:
        logger.exception("device_manager: unexpected screenshot error")
        return {"error": str(exc), "content": None}
