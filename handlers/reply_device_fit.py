"""The device-list hint: which listed devices each lane can drive."""

from __future__ import annotations


def fit_note(devices: list) -> str:
    """A note for a list of two or more devices, or ``""``.

    The run lane drives Android only; capture accepts any listed device. With
    several devices the host must not guess which one the tester means.
    """
    if len(devices) < 2:
        return ""
    android = [
        str(dev.get("id"))
        for dev in devices
        if isinstance(dev, dict) and dev.get("platform") == "android"
    ]
    here = ", ".join(f"`{one}`" for one in android) or "none"
    ask = " With more than one Android device, ask the user which to use."
    return (
        f"> `qa_mobile_test` drives Android devices only: here {here}. "
        "`qa_capture_screens` accepts any listed device."
        + (ask if len(android) > 1 else "")
    )
