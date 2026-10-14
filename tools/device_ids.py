"""Device-id and package-name whitelists plus the ``adb devices`` parser.

Stdlib-only facade: no tools.* imports beyond its own leaves, so tools.mobile.adb
can use them without importing device_manager. The code lives in
``tools/device_ids_check.py`` (whitelists) and ``tools/device_ids_adb.py``
(the adb parser); every old name is re-exported here.
"""

from __future__ import annotations

import re  # noqa: F401

from tools.device_ids_adb import (  # noqa: F401  re-exports: moved names keep their old path
    _ADB_KNOWN_STATES,
    _MAX_ADB_LINES,
    _MAX_ADB_STATE_CHARS,
    AdbRows,
    _adb_candidate_lines,
    _adb_row,
    parse_adb_devices,
)
from tools.device_ids_check import (  # noqa: F401  re-exports: moved names keep their old path
    _ANDROID_SYSTEM_PREFIXES,
    _DEVICE_ID_RE,
    _MAX_PACKAGE_LEN,
    _PACKAGE_NAME_RE,
    SYSTEM_PACKAGE_PREFIXES,
    _valid_device_id,
    _valid_package_name,
    parse_pm_packages,
    valid_package_name,
)
