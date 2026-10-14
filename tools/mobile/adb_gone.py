"""Device-gone listeners and the adb stderr pattern that fires them (moved from adb.py)."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from tools.mobile.adb_host import host

logger = logging.getLogger("tools.mobile.adb")

#: What adb prints when the selected device is no longer usable. Any of these
#: drops the device-listing cache (S5), so a stale listing is not served.
_DEVICE_GONE_RE = re.compile(
    r"device '[^']*' not found|device offline|device unauthorized"
    r"|device still authorizing|no devices/emulators found",
    re.IGNORECASE,
)

#: Callbacks run when adb reports a device gone, keyed by the callable itself so
#: re-subscribing the same one is idempotent while distinct closures that share
#: a qualname (``<locals>``, ``<lambda>``) no longer replace each other. ``tools.device_manager`` subscribes its
#: ``invalidate_discovery_cache`` at import. NOTE: ``importlib.reload`` of this
#: module (not of the ``adb`` facade, which only re-exports the dict) rebinds it
#: to an empty one; reload device_manager afterwards to re-subscribe.
_device_gone_listeners: dict[Callable[[], None], Callable[[], None]] = {}
#: Public name for the same dict (a pinned foreign test still reads the private).
device_gone_listeners = _device_gone_listeners


def on_device_gone(cb: Callable[[], None]) -> None:
    """Register *cb* to run when adb reports the selected device gone."""
    host().device_gone_listeners[cb] = cb


def _notify_device_gone() -> None:
    for cb in list(host().device_gone_listeners.values()):
        try:
            cb()
        except Exception:
            logger.exception("mobile.adb device-gone listener failed")
