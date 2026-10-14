"""A fast screen dump through the qa-ime AccessibilityService, with uiautomator as the fallback.

**Why.** ``uiautomator dump`` costs seconds a look; the service answers the same question
from inside the device. The reply is uiautomator-shaped XML, so ``perception.prune`` (and the
destructive guard downstream of it) consume it unchanged: this module changes WHERE the XML
comes from, never which node is judged. ``enable`` refuses the provider unless a parity check
against a real uiautomator dump agrees.

**One window.** The service dumps only the ACTIVE window (``rootInActiveWindow``), which is
what ``uiautomator dump`` does by default. That default is ASSUMED, not device-verified; the
parity check is what catches a device where it is not true.

**Modelled on ``animations``.** A per-run record is written BEFORE the setting changes, the
restore reads only that record, and a stale sweep gives back what a crashed run left behind.
Every public function returns ``{"error", "content"}`` and never raises.
"""

from tools.mobile.a11y_device import (  # noqa: F401
    A11Y_MAX_SERVICES_VALUE_CHARS,
    MASTER,
    SETTING,
    append_service,
    component,
    read_state,
)
from tools.mobile.a11y_dump import (  # noqa: F401
    _FAILS,
    A11Y_DUMP_TIMEOUT_S,
    A11Y_MAX_CONSECUTIVE_FAILURES,
    A11Y_MAX_REPLY_CHARS,
    decode,
    fetch_dump,
    provider,
)
from tools.mobile.a11y_enable import (  # noqa: F401
    A11Y_CONNECT_MAX_POLLS,
    A11Y_CONNECT_POLL_INTERVAL_S,
    _verify_parity,
    enable,
)
from tools.mobile.a11y_parity import A11Y_PARITY_OVERLAP_FRACTION, compare  # noqa: F401
from tools.mobile.a11y_record import (  # noqa: F401
    RECORD_FILE,
    clear_record,
    read_record,
    write_record,
)
from tools.mobile.a11y_restore import _RUNS, restore  # noqa: F401
from tools.mobile.a11y_stale import restore_stale, stale_records  # noqa: F401
