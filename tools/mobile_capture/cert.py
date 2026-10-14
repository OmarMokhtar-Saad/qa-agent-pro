"""Install / status / remove the qa-agents root CA on a DEVICE's trust store.

**The store Android apps actually obey is the SYSTEM store**, not the user
store: the user certificate store is ignored by every app targeting API 24+
unless the app's own ``network_security_config`` opts in, which almost none
do. Being debuggable does not change this -- it only activates a
``<debug-overrides>`` block if the APK shipped one. So this module installs
ONLY into the system store, and refuses BY NAME when it cannot, rather than
quietly installing somewhere that would not change what a tester sees.

**The route depends on the device's API level, not a guess:**

* API 30+: the system partition is read-only even with root. The working
  route is ``/data/misc/user/0/cacerts-added/<hash>.0`` -- writable with
  root alone, no remount needed.
* API < 30: ``/system/etc/security/cacerts/<hash>.0``, which needs root AND
  a writable ``/system`` (``adb remount``).

**Root is checked by FACT, never by attempting and parsing stderr.**
``ro.build.type`` is read first: only ``userdebug``/``eng`` builds grant
``adb root`` at all; a ``user`` (production) build refuses it outright, on
an emulator exactly as on a real phone. This module reads that property and
refuses by name (:data:`REASON_PRODUCTION_BUILD`) BEFORE ever calling
``adb root``, rather than trying it and improvising off the refusal text.

**A Play Store emulator is such a device.** The lane boots the AVD the
tester created in Android Studio; a ``google_apis_playstore`` image is a
Play (production) system image, and
``tools/mobile/emulator.py`` never passes ``-writable-system`` on its start
command. Neither is changed here: a Play image is what lets a tester
install and open an app from the Play Store inside a run at all, and
``-writable-system`` changes how every emulator this lane owns boots. Both
are owner decisions with their own blast radius (see the plan ledger). The
practical consequence, stated rather than hidden: on the emulator this lane
boots today, ``install()`` reaches :data:`REASON_PRODUCTION_BUILD` every
time, honestly. The full two-route path is implemented and graded so the
capability is ready the moment the owner picks an image that can use it, or
for a real, rooted device or a locally-run non-Play emulator.

**A production phone gets the same honest refusal**, never a half attempt: a
real device without root refuses at the identical ``ro.build.type`` check.

Every public function returns ``{"error", "content"}`` and never raises.
Every device-touching call needs ``apply=True``; ``apply=False`` (or a
caller that never sets it) touches nothing. Runs under the lane's own
per-serial device lock (``tools.mobile.device_lock.take_device_lock``), the same
primitive ``proxy.start`` uses -- no second lock is invented.

**Never a source of TIER_DECRYPTED.** A successful ``install()`` proves the
certificate is PRESENT on the device, never that anything trusts it or that
a request was actually decrypted -- ``ladder.decide`` reaches
``TIER_DECRYPTED`` from the functional probe alone, and nothing here changes
that. See ``tests/mobile_capture/test_cert.py`` for the mutant that proves
it.
"""

from __future__ import annotations

import logging

from tools.mobile import adb, device_lock  # noqa: F401
from tools.mobile_capture import ca, ledger, paths, proxy  # noqa: F401
from tools.mobile_capture.cert_const import (  # noqa: F401
    _HASH_NAME_RE,
    _ROOTABLE_BUILD_TYPES,
    API_LEVEL_FOR_DATA_ROUTE,
    DATA_CACERTS_DIR,
    DEVICE_CALL_TIMEOUT_S,
    REASON_API_UNKNOWN,
    REASON_DEVICE_UNREACHABLE,
    REASON_FLAG_OFF,
    REASON_NO_APPLY,
    REASON_NO_CA,
    REASON_PRODUCTION_BUILD,
    REASON_PUSH_FAILED,
    REASON_REMOUNT_FAILED,
    REASON_USER_STORE_MANUAL,
    REASON_VERIFY_FAILED,
    ROUTE_DATA,
    ROUTE_SYSTEM,
    SYSTEM_CACERTS_DIR,
    USER_STORE_STAGE_DIR,
)
from tools.mobile_capture.cert_install import _install_checked, install  # noqa: F401
from tools.mobile_capture.cert_marker import (  # noqa: F401
    _clear_marker,
    _marker_path,
    _rc,
    _write_marker,
    read_cert_marker,
)
from tools.mobile_capture.cert_push import _push_failed, _push_pem  # noqa: F401
from tools.mobile_capture.cert_remove import (  # noqa: F401
    _make_writable,
    _remove_from_device,
    _remove_recorded,
    remove,
    status,
)
from tools.mobile_capture.cert_route import (  # noqa: F401
    _ca_identity,
    _pick_route,
    _root_device,
)
from tools.mobile_capture.cert_stage import (  # noqa: F401
    _manual_install_detail,
    _stage_for_manual_install,
)

logger = logging.getLogger("tools.mobile_capture.cert")
