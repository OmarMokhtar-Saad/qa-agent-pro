"""The booted emulator's system-image rows of the machine report."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from tools.machine_report_core import _PROBE_ERRORS, NO_MOBILE_DETAIL, Row

# The mobile modules are absent from some builds: then the image row reads "off".
try:
    import tools.mobile.emulator as emulator
except ImportError as _exc:
    if not (_exc.name or "").startswith("tools.mobile"):
        raise
    emulator = cast("Any", None)
try:
    import tools.mobile.sdk_locator as sdk_locator
except ImportError as _exc:
    if not (_exc.name or "").startswith("tools.mobile"):
        raise
    sdk_locator = cast("Any", None)

_COMPONENT = "mobile_system_image"
_UNFINISHED = "The emulator probe did not finish, so the image is unknown."
_FAILED = "The emulator probe failed, so the image is unknown."
_NONE_BOOTED = "No emulator is booted, so its system image is unknown."

#: Seconds the machine report waits for the booted-emulator listing behind the
#: system-image row. The report runs on a worker thread a polling client waits
#: on, so a wedged adb costs a bounded, stated delay rather than a hang.
#: Bounded from above in ``tests/test_bounds_upper.py``.
SYSTEM_IMAGE_PROBE_TIMEOUT_S = 10


# ``Row``s naming each booted emulator's system image. Never raises.
#
# The booted AVD's name comes from ``emulator.list_running`` -- the producer
# qa-doctor asks -- and its image from ``sdk_locator.avd_system_image``, on
# disk. With nothing booted the row says so and carries NO tag, and no detail
# here ever carries an AVD name: the desktop also accepts the bare word
# ``google_apis`` anywhere in a detail, and a tester may name an AVD that.
# ``asyncio.run`` is safe because ``collect`` runs on a worker thread.
def image_items() -> list:
    component = _COMPONENT
    if emulator is None or sdk_locator is None:
        return [Row(component, "off", NO_MOBILE_DETAIL)]
    booted = _probe_booted(emulator, component)
    if isinstance(booted, Row):
        return [booted]
    return _booted_image_items(booted, component, sdk_locator)


# The booted emulators, or the one ``Row`` that says why there are none.
def _probe_booted(emulator, component: str) -> "list | Row":
    try:
        listed = asyncio.run(
            asyncio.wait_for(
                emulator.list_running(), timeout=SYSTEM_IMAGE_PROBE_TIMEOUT_S
            )
        )
    except _PROBE_ERRORS:
        return Row(component, "undetermined", _UNFINISHED)
    if listed.get("error"):
        return Row(component, "undetermined", _FAILED)
    booted = [item for item in (listed.get("content") or []) if isinstance(item, dict)]
    return booted or Row(component, "off", _NONE_BOOTED)


def _booted_image_items(booted: list, component: str, sdk_locator) -> list:
    """One ``Row`` per booted emulator, naming its system image."""
    items = []
    for item in booted:
        serial = str(item.get("serial") or "")[:40]
        found = sdk_locator.avd_system_image(item.get("avd")) or {}
        image = str(found.get("content") or "")
        if image:
            items.append(Row(component, "ok", serial + " runs " + image))
        else:
            items.append(
                Row(
                    component,
                    "undetermined",
                    serial + " is booted, but its system image could not be read.",
                )
            )
    return items
