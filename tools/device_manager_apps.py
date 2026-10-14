"""Installed-app listing (device-driven app picker) for ``tools.device_manager``."""

from __future__ import annotations

import logging
import re

from tools.device_ids import _valid_device_id, _valid_package_name, parse_pm_packages
from tools.device_manager_host import host
from tools.device_manager_spawn import (
    _checked_run,
    _cmd_timeout,
    _error_reply,
    _rc_zero,
    _unpack_device,
)

logger = logging.getLogger("tools.device_manager")

# CFBundleIdentifier occurrences in `xcrun simctl listapps` plist-ish output.
_CFBUNDLE_ID_RE = re.compile(r'CFBundleIdentifier\s*=\s*"([^"]+)"')


# Parse ``pm list packages`` output ('package:<id>' per line) into app dicts.
#
# Every id is whitelist-validated before inclusion; invalid ids (and, when
# *drop_system*, obvious system namespaces) are skipped. Duplicates collapse.
def _parse_pm_list(out: bytes, *, drop_system: bool = False) -> list[dict]:
    """Parse ``pm list packages`` output into app dicts."""
    ids = parse_pm_packages(out.decode(errors="replace"), drop_system=drop_system)
    return [{"id": p, "name": p} for p in ids]


async def _android_all_packages(device_id: str) -> list[dict]:
    """Fallback: the full package list minus obvious system namespaces."""
    cmd = [host()._adb_binary(), "-s", device_id, "shell", "pm", "list", "packages"]
    got = await _checked_run(cmd, _cmd_timeout())
    if got is None or got[0] != 0:
        return []
    return host()._parse_pm_list(got[1], drop_system=True)


# Third-party packages via ``adb -s <id> shell pm list packages -3``.
#
# Some emulators report an empty third-party list; in that case fall back to
# the full ``pm list packages`` and drop obvious system namespaces. A missing
# adb, a timeout, or a non-zero return code all degrade to an empty list.
async def list_android_apps(device_id: str) -> list[dict]:
    """Third-party packages via ``adb -s <id> shell pm list packages -3``."""
    cmd = [host()._adb_binary(), "-s", device_id, "shell", "pm", "list", "packages"]
    got = await _checked_run(
        [*cmd, "-3"],
        _cmd_timeout(),
        "device_manager: adb not installed -- cannot list Android apps",
    )
    msg = "device_manager: adb 'pm list packages -3' failed (rc=%s)"
    if got is None or not _rc_zero(got, msg, logging.WARNING):
        return []
    return host()._parse_pm_list(got[1]) or await _android_all_packages(device_id)


def _bundle_ids(text: str) -> list[dict]:
    ids = dict.fromkeys(m.group(1) for m in _CFBUNDLE_ID_RE.finditer(text))
    return [{"id": p, "name": p} for p in ids if _valid_package_name(p)]


# Installed apps on a booted simulator via ``xcrun simctl listapps <udid>``.
#
# Best-effort: every CFBundleIdentifier occurrence is collected and
# whitelist-validated. A parse failure yields an empty list, never an error.
async def _list_ios_simulator_apps(device_id: str) -> list[dict]:
    """Installed apps on a booted simulator via ``xcrun simctl listapps <udid>``."""
    got = await _checked_run(
        ["xcrun", "simctl", "listapps", device_id],
        _cmd_timeout(),
        "device_manager: xcrun not installed -- cannot list iOS apps",
    )
    if got is None or not _rc_zero(
        got, "device_manager: simctl listapps failed (rc=%s)", logging.INFO
    ):
        return []
    return _bundle_ids(got[1].decode(errors="replace"))


async def _apps_for(device_id: str, platform: str, kind: str) -> list[dict] | None:
    """The apps for one device, or None when the platform is unsupported."""
    if platform == "android":
        return await host().list_android_apps(device_id)
    if platform == "ios" and kind == "simulator":
        return await host()._list_ios_simulator_apps(device_id)
    # Physical iOS app enumeration is unsupported for now.
    return [] if platform == "ios" else None


# Return the apps installed on *device* for the exploratory app picker.
#
# Shape: ``{"content": [{"id": ..., "name": ...}, ...], "error": None}``.
# Never raises. Android and iOS *simulators* are supported; physical iOS is
# unsupported for now and returns an empty list (NOT an error). A missing CLI
# tool, timeout, or unreadable output all degrade to an empty list so the
# caller can fall back to a manual prompt. The device id is
# whitelist-validated before it reaches any subprocess.
async def list_installed_apps(device: dict) -> dict:
    """Return the apps installed on *device* for the exploratory app picker."""
    device_id, platform, kind = _unpack_device(device)
    if not _valid_device_id(device_id):
        return _error_reply("invalid or missing device id")
    try:
        apps = await _apps_for(device_id, platform, kind)
        if apps is None:
            return _error_reply(f"unsupported device platform: {platform or 'unknown'}")
        logger.info(
            "device_manager: listed %d installed app(s) on %s", len(apps), platform
        )
        return {"content": apps, "error": None}
    except Exception as exc:
        logger.exception("device_manager: unexpected error listing installed apps")
        return _error_reply(str(exc))
