"""The refusals for a uiautomator dump that produced no XML (moved from adb_dump.py)."""

from __future__ import annotations


def _device_said(said: str) -> dict:
    """The refusal that quotes the device's own words first."""
    # The device SPOKE, so its words LEAD (the first sentence is what the model reads).
    return {
        "error": (
            "The device said: "
            + said
            + " -- uiautomator produced no XML for this screen. If "
            "those words name a missing dump file, the dumper did not "
            "run; a secure window (a password field or a payment "
            "sheet) is one cause, and moving past it or using a screen "
            "that allows accessibility is the fix."
        ),
        "content": None,
    }


def _dump_no_xml(said: str) -> dict:
    """The refusal for a dump that did not produce XML; *said* is the device's
    own (collapsed, capped) output, possibly empty."""
    if said:
        return _device_said(said)
    # Nothing came back at all. There are no device words to lead with, so the
    # likeliest cause is all this branch has to offer.
    return {
        "error": (
            "uiautomator returned no XML for this screen and the device "
            "said nothing. A secure window (a password field or a payment "
            "sheet) blocks the dump; move past it or use a screen that "
            "allows accessibility."
        ),
        "content": None,
    }
