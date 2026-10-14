"""Constants of the device certificate install: timeout, routes, refusal codes."""

from __future__ import annotations

import re

#: Per-call timeout for a single root/remount/push/verify adb round trip.
#: See tests/test_bounds_upper.py::CEILINGS.
DEVICE_CALL_TIMEOUT_S: int = 20

#: Android build types that grant `adb root` at all. Anything else (most
#: notably "user", the production build every real phone and this lane's own
#: Play emulator ship) refuses it outright.
_ROOTABLE_BUILD_TYPES = frozenset({"userdebug", "eng"})

#: The API level at and above which the writable route moves from the
#: read-only-even-with-root `/system` partition to `/data`, which needs only
#: root, never a remount. Not itself a resource cap -- a fixed Android
#: platform fact -- so it is spelled to stay outside the cap scanner's own
#: MAX/MIN/CAP/LIMIT/TIMEOUT/... vocabulary.
API_LEVEL_FOR_DATA_ROUTE: int = 30

SYSTEM_CACERTS_DIR = "/system/etc/security/cacerts"
DATA_CACERTS_DIR = "/data/misc/user/0/cacerts-added"

ROUTE_SYSTEM = "system"
ROUTE_DATA = "data"

#: Refusal codes. A caller branches on these BY NAME. `mobile_capture.prepare`
#: maps every one of these to `ladder.REASON_CERT_NOT_TRUSTED` -- the ONE
#: producer of the tier-level sentence -- while this module keeps the more
#: specific reason available to a direct caller (`qa_setup_capture`) or a
#: test.
REASON_NO_APPLY = "no_apply"
#: The lane kill-switch is unset. Installing a root CA into a device's trust
#: store is one of the two effects the flag still gates (with provisioning).
REASON_FLAG_OFF = "flag_off"
REASON_NO_CA = "no_ca"
REASON_DEVICE_UNREACHABLE = "device_unreachable"
REASON_PRODUCTION_BUILD = "production_build"

#: Staged for a MANUAL user-store install. NOT a success and NOT a refusal:
#: the file is on the device, nothing is trusted yet, and only the tester can
#: finish it. Android 11+ removed every route that would let adb install a user
#: certificate, so a manual step is the whole of what is available here.
REASON_USER_STORE_MANUAL = "user_store_manual"

#: Where a staged certificate lands. The Downloads folder because that is what
#: the Settings certificate picker opens into; anywhere else and the tester has
#: to go looking.
USER_STORE_STAGE_DIR = "/sdcard/Download"
REASON_REMOUNT_FAILED = "remount_failed"
REASON_API_UNKNOWN = "api_unknown"
REASON_PUSH_FAILED = "push_failed"
REASON_VERIFY_FAILED = "verify_failed"

_HASH_NAME_RE = re.compile(r"^[0-9a-f]{8}\.0$")
