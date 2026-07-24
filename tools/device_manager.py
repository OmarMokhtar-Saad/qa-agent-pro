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

from config.settings import settings
from tools.secure_temp import make_secure_temp_path

logger = logging.getLogger(__name__)

# Whitelist a device identifier before it is ever passed to a subprocess.
# adb serials use alphanumerics plus . _ : - ; iOS UDIDs are hex + hyphen (or a
# 25-char hex form). This rejects spaces, quotes, slashes, and every shell
# metacharacter outright, so no crafted "id" can smuggle extra arguments. The
# FIRST character may NOT be a hyphen, so an id can never be mistaken for a
# command-line option/flag by the subprocess (defence in depth on top of the
# arg-list, no-shell exec).
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._:][A-Za-z0-9._:\-]{0,127}$")

# UDID shapes seen in `xcrun devicectl list devices` output.
_UDID_RE = re.compile(
    r"\b([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
    r"|[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}"
    r"|[0-9A-Fa-f]{25})\b"
)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _valid_device_id(device_id: object) -> bool:
    """True when *device_id* is a non-empty string matching the safe whitelist."""
    return bool(isinstance(device_id, str) and _DEVICE_ID_RE.match(device_id))


def _cmd_timeout() -> int:
    return settings.qa_device_command_timeout


def _shot_timeout() -> int:
    return settings.qa_device_screenshot_timeout


async def _run(cmd: list[str], timeout: int) -> tuple[int, bytes, bytes]:
    """Run *cmd* (argument list, NO shell) and return (returncode, stdout, stderr).

    Raises ``FileNotFoundError`` when the binary is missing (callers convert this
    to a friendly error) and ``asyncio.TimeoutError`` when the call overruns
    *timeout*.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await asyncio.wait_for(proc.communicate(), timeout=1)
        except Exception:
            pass
        logger.warning("device_manager: command timed out: %s", cmd[0])
        raise
    return proc.returncode or 0, stdout or b"", stderr or b""


# --------------------------------------------------------------------------- #
# Listing
# --------------------------------------------------------------------------- #


async def _list_android() -> list[dict]:
    """Devices/emulators visible to ``adb devices -l``. Empty list if adb missing."""
    try:
        rc, out, err = await _run(["adb", "devices", "-l"], _cmd_timeout())
    except FileNotFoundError:
        logger.info("device_manager: adb not installed -- skipping Android devices")
        return []
    except asyncio.TimeoutError:
        return []
    if rc != 0:
        logger.warning(
            "device_manager: adb devices failed: %s",
            err.decode(errors="replace")[:200],
        )
        return []
    devices: list[dict] = []
    for raw in out.decode(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("list of devices"):
            continue
        parts = line.split()
        if len(parts) < 2 or parts[1] != "device":
            continue
        serial = parts[0]
        if not _valid_device_id(serial):
            continue
        name = serial
        for token in parts[2:]:
            if token.startswith("model:"):
                name = token.split(":", 1)[1].replace("_", " ") or serial
                break
        kind = "emulator" if serial.startswith("emulator-") else "device"
        devices.append(
            {"id": serial, "name": name, "platform": "android", "kind": kind}
        )
    return devices


async def _list_ios_simulators() -> list[dict]:
    """Booted iOS simulators via ``xcrun simctl list devices booted -j``."""
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


async def list_devices() -> dict:
    """Return every attached Android/iOS device, emulator, and simulator.

    Shape: ``{"content": [{id, name, platform, kind}, ...], "error": None}``.
    Never raises. A per-platform tool being missing is NOT an error -- that
    platform is simply skipped. Only a truly unexpected failure sets ``error``.
    """
    try:
        android, simulators, physical = await asyncio.gather(
            _list_android(),
            _list_ios_simulators(),
            _list_ios_physical(),
        )
        devices = [*android, *simulators, *physical]
        logger.info("device_manager: discovered %d device(s)", len(devices))
        return {"content": devices, "error": None}
    except Exception as exc:
        logger.exception("device_manager: unexpected error listing devices")
        return {"error": str(exc), "content": None}


# --------------------------------------------------------------------------- #
# Installed-app listing (device-driven app picker for the exploratory wizard)
# --------------------------------------------------------------------------- #

# A package / bundle identifier is reverse-DNS: dot-separated segments of
# letters, digits, and underscores, with at least one dot. This whitelist
# rejects EVERY shell metacharacter, whitespace, quote, slash, and a leading
# hyphen outright, so a device-reported id can never smuggle extra arguments
# into a later subprocess call (defence in depth on top of the arg-list,
# no-shell exec). Length is capped well above any real-world identifier.
_PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+$")
_MAX_PACKAGE_LEN = 205

# `pm list packages` (without -3) also returns framework packages; drop the
# obvious system namespaces when we fall back to the full list on emulators
# whose third-party list comes back empty.
_ANDROID_SYSTEM_PREFIXES = ("com.android.", "com.google.android.", "android.")

# CFBundleIdentifier occurrences in `xcrun simctl listapps` plist-ish output.
_CFBUNDLE_ID_RE = re.compile(r'CFBundleIdentifier\s*=\s*"([^"]+)"')


def _valid_package_name(name: object) -> bool:
    """True when *name* is a safe, whitelist-matching package / bundle id."""
    return bool(
        isinstance(name, str)
        and 0 < len(name) <= _MAX_PACKAGE_LEN
        and _PACKAGE_NAME_RE.match(name)
    )


def valid_package_name(name: object) -> bool:
    """Public wrapper over :func:`_valid_package_name`.

    Callers outside this module (e.g. app.py re-validating a client-controllable
    Chainlit action payload before reusing the id in a subprocess) should use
    this public name rather than importing the underscore-private helper."""
    return _valid_package_name(name)


def _parse_pm_list(out: bytes, *, drop_system: bool = False) -> list[dict]:
    """Parse ``pm list packages`` output ('package:<id>' per line) into app dicts.

    Every id is whitelist-validated before inclusion; invalid ids (and, when
    *drop_system*, obvious system namespaces) are skipped. Duplicates collapse."""
    apps: list[dict] = []
    seen: set[str] = set()
    for raw in out.decode(errors="replace").splitlines():
        line = raw.strip()
        if not line.startswith("package:"):
            continue
        pkg = line[len("package:") :].strip()
        if drop_system and pkg.startswith(_ANDROID_SYSTEM_PREFIXES):
            continue
        if pkg in seen or not _valid_package_name(pkg):
            continue
        seen.add(pkg)
        apps.append({"id": pkg, "name": pkg})
    return apps


async def _list_android_apps(device_id: str) -> list[dict]:
    """Third-party packages via ``adb -s <id> shell pm list packages -3``.

    Some emulators report an empty third-party list; in that case fall back to
    the full ``pm list packages`` and drop obvious system namespaces. A missing
    adb, a timeout, or a non-zero return code all degrade to an empty list."""
    try:
        rc, out, _err = await _run(
            ["adb", "-s", device_id, "shell", "pm", "list", "packages", "-3"],
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
            ["adb", "-s", device_id, "shell", "pm", "list", "packages"],
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
        ["adb", "-s", device_id, "exec-out", "screencap", "-p"], _shot_timeout()
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
