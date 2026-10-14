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
import json  # noqa: F401
import logging
import os  # noqa: F401
import re  # noqa: F401
import sys
import time  # noqa: F401
from typing import Any, cast  # noqa: F401

from tools import device_manager_host
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
from tools.device_manager_android import (  # noqa: F401
    _android_row,
    _AndroidListing,
    _list_android,
    _start_server_hint,
    list_android_all,
    start_adb_server,
)
from tools.device_manager_apps import (  # noqa: F401
    _CFBUNDLE_ID_RE,
    _list_ios_simulator_apps,
    _parse_pm_list,
    list_android_apps,
    list_installed_apps,
)
from tools.device_manager_discover import (  # noqa: F401
    _list_devices_discover,
    list_devices,
)
from tools.device_manager_ios import (  # noqa: F401
    _UDID_RE,
    list_ios_physical,
    list_ios_simulators,
)
from tools.device_manager_legacy import _run, _start_adb_server  # noqa: F401
from tools.device_manager_remedy import (  # noqa: F401
    _ADB_REMEDIATION,
    _ADB_REMEDIATION_UNKNOWN,
    adb_state_remediation,
)
from tools.device_manager_shot import (  # noqa: F401
    _PNG_MAGIC,
    _png_result,
    _screenshot_android,
    _screenshot_ios_physical,
    _screenshot_ios_simulator,
    capture_screenshot,
)
from tools.device_manager_spawn import (  # noqa: F401
    _cmd_timeout,
    _shot_timeout,
    run_cmd,
)
from tools.process_runner import arun  # noqa: F401
from tools.secure_temp import make_secure_temp_path  # noqa: F401

# The mobile modules are absent from some builds: then the bare ``adb`` is used.
try:
    import tools.mobile.adb as mobile_adb
except ImportError as _exc:
    if not (_exc.name or "").startswith("tools.mobile"):
        raise
    mobile_adb = cast("Any", None)

logger = logging.getLogger(__name__)


def _adb_binary() -> str:
    """The adb every spawn here uses: the SDK's own, as the mobile lane resolves it.

    A bare ``"adb"`` depends on this process's PATH, which an IDE-spawned
    server often lacks, so qa_list_devices reported "No devices detected"
    for a device qa_mobile_test was driving. A build without the mobile modules
    degrades to the bare ``"adb"`` on PATH instead of raising.
    """
    if mobile_adb is None:
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


device_manager_host.bind(sys.modules[__name__])
