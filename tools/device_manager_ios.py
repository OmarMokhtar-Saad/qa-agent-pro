"""iOS simulator and physical-device listing for ``tools.device_manager``."""

from __future__ import annotations

import json
import logging
import re

from tools.device_ids import _valid_device_id
from tools.device_manager_host import host
from tools.device_manager_spawn import _checked_run, _cmd_timeout, _rc_ok

logger = logging.getLogger("tools.device_manager")

# UDID shapes seen in `xcrun devicectl list devices` output.
_UDID_RE = re.compile(
    r"\b([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
    r"|[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}"
    r"|[0-9A-Fa-f]{25})\b"
)

_DEVICECTL_HEADERS = ("devices", "name", "---", "identifier")


def _sim_row(sim: dict) -> dict | None:
    if (sim.get("state") or "").lower() != "booted":
        return None
    udid = sim.get("udid")
    if not _valid_device_id(udid):
        return None
    return {
        "id": udid,
        "name": sim.get("name") or udid,
        "platform": "ios",
        "kind": "simulator",
    }


def _booted_simulators(out: bytes) -> list[dict]:
    try:
        data = json.loads(out.decode(errors="replace") or "{}")
    except json.JSONDecodeError:
        logger.warning("device_manager: could not parse simctl JSON output")
        return []
    sims = (
        s for runtime in (data.get("devices") or {}).values() for s in runtime or []
    )
    return [row for row in map(_sim_row, sims) if row]


async def list_ios_simulators() -> list[dict]:
    """Booted iOS simulators via ``xcrun simctl list devices booted -j``."""
    if not await host()._xcode_present():
        return []
    got = await _checked_run(
        ["xcrun", "simctl", "list", "devices", "booted", "-j"],
        _cmd_timeout(),
        "device_manager: xcrun not installed -- skipping iOS simulators",
    )
    msg = "device_manager: simctl list failed: %s"
    if got is None or not _rc_ok(got, msg, logging.WARNING):
        return []
    return _booted_simulators(got[1])


def _devicectl_row(raw: str) -> dict | None:
    line = raw.strip()
    if not line or line.lower().startswith(_DEVICECTL_HEADERS):
        return None
    match = _UDID_RE.search(line)
    if not match or not _valid_device_id(match.group(1)):
        return None
    udid = match.group(1)
    name = line[: match.start()].strip() or udid
    return {"id": udid, "name": name, "platform": "ios", "kind": "device"}


# Physical iOS devices via ``xcrun devicectl list devices`` (Xcode 15+).
#
# devicectl's tabular text output is parsed leniently: any UDID-shaped token on
# a data row is treated as the identifier and the preceding text as the name.
# Best-effort -- returns an empty list if devicectl is unavailable or the
# format is unrecognised.
async def list_ios_physical() -> list[dict]:
    """Physical iOS devices via ``xcrun devicectl list devices`` (Xcode 15+)."""
    if not await host()._xcode_present():
        return []
    got = await _checked_run(
        ["xcrun", "devicectl", "list", "devices"],
        _cmd_timeout(),
        "device_manager: xcrun/devicectl not installed -- skipping physical iOS",
    )
    msg = "device_manager: devicectl list unavailable: %s"
    if got is None or not _rc_ok(got, msg, logging.INFO):
        return []
    rows = map(_devicectl_row, got[1].decode(errors="replace").splitlines())
    return [row for row in rows if row]
