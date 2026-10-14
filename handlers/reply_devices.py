"""The device listing reply (moved from handlers._reply)."""

from __future__ import annotations

from typing import NamedTuple

from handlers import _lane_tools
from handlers.reply_adb import _adb_notice, _attached_devices_lines


class DeviceListing(NamedTuple):
    """The inputs of `shape_devices` (a parameter object)."""

    devices: list
    unusable: list | None = None
    adb_dropped_lines: int = 0
    booting: list | None = None
    empty_hint: str = ""
    adb_problem: str = ""


def _listing_lines(devices: list, attached: list) -> list[str]:
    """The pick list, then the not-usable section when there is one."""
    if not devices:
        lines = ["## Devices (0)", "", "Nothing is usable yet \u2014 see below."]
    else:
        lines = [f"## Devices ({len(devices)})", ""]
    lines.extend(_device_row_line(dev) for dev in devices)
    if attached:
        lines.extend(_attached_devices_lines(attached))
    return lines


# WHY A SEPARATE SECTION rather than a row in the list above it: this list is
# also the PICK LIST -- `_device_menu_markdown` renders these very rows as the
# tester's choices. An `unauthorized` phone offered as a choice fails opaquely
# inside a capture or a mobile run, and would push the non-`device` case into
# four more consumers whose right answer is "ignore". A separate section is
# informational by construction: impossible to select, impossible to miss.
#
# And the empty text is conditional on BOTH lists, which is the whole defect:
def shape_devices(listing: DeviceListing) -> str:
    """The device list a tester reads. *unusable* is rendered SEPARATELY.

    The empty text is conditional on BOTH lists, which is the whole defect:
    a tester must never read "No devices detected" while a phone is plugged in.
    """
    devices = listing.devices
    attached = list(listing.unusable or [])
    notice = _adb_notice(listing.adb_dropped_lines, listing.adb_problem)
    if not devices and not attached:
        return _no_devices_reply(listing.booting, listing.empty_hint) + notice
    return "\n".join(_listing_lines(devices, attached)) + notice


def _no_devices_reply(booting: list | None, empty_hint: str) -> str:
    """The reply for "nothing listed at all", without the adb notice."""
    # The notice belongs on THIS branch most of all: 200+ unparseable lines
    # yield zero rows, and "No devices detected" while a phone is plugged
    # in is the very defect this section exists to prevent.
    # BOOTING, not absent (fix round 3, item 5): `booting` is
    # `emulator.recently_started()`, passed only when both lists are
    # empty. The heading is `render.starting_heading`, the sentence
    # `qa_mobile_test` already says for the same state.
    if booting:
        block = _lane_tools.render.booting_devices_block(booting)
        if block:
            return block
    return (
        "No devices detected. Connect an Android device/emulator or boot an "
        "iOS simulator, then retry `qa_list_devices`."
        + ("\n\n" + str(empty_hint) if empty_hint else "")
    )


def _device_row_line(dev: dict) -> str:
    """One bullet of the device pick list."""
    row = (
        f"- `{dev.get('id')}` — {dev.get('name')} "
        f"({dev.get('platform')}/{dev.get('kind')})"
    )
    # Printed only when a producer answered. An absent AVD name or API
    # level renders the row exactly as it did before, which is what makes
    # the enrichment safe to fail.
    detail = ", ".join(
        part
        for part in (
            ("AVD `" + str(dev.get("avd"))[:60] + "`") if dev.get("avd") else "",
            ("API " + str(dev.get("api"))[:8]) if dev.get("api") else "",
        )
        if part
    )
    return row + (" — " + detail if detail else "")
