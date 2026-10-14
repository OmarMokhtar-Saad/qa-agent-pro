"""Tester-facing adb-state remediation text for ``tools.device_manager``."""

from __future__ import annotations

#: Written for a tester who does not write code: what to DO, on the phone, now.
_ADB_REMEDIATION = {
    "unauthorized": (
        "Unlock the phone's screen. It is asking 'Allow USB debugging?' -- tick "
        "'Always allow from this computer' and tap Allow. If no dialog appears, "
        "unplug the cable and plug it back in while the screen is unlocked."
    ),
    "authorizing": (
        "The phone is still deciding whether to trust this computer. Unlock its "
        "screen, answer the 'Allow USB debugging?' dialog, then run "
        "`qa_list_devices` again."
    ),
    "offline": (
        "The connection dropped. Unplug the cable and re-plug it -- a different "
        "USB port or cable often fixes this -- then run `qa_list_devices` again."
    ),
    "connecting": (
        "This computer is still connecting to the device. Wait a few seconds and "
        "run `qa_list_devices` again."
    ),
    "no_permissions": (
        "This computer is not allowed to talk to the device. On Linux the udev "
        "rules are missing; on Windows it is the USB driver. Ask whoever "
        "supports your machine for the Android USB rules/driver for this phone, "
        "then re-plug it."
    ),
}

_ADB_REMEDIATION_UNKNOWN = (
    "This computer can see the device, but adb reports a state we do not "
    "recognise, so we cannot say what it needs. Unlock the phone, re-plug the "
    "cable and run `qa_list_devices` again; if it stays this way, send this "
    "whole message to whoever supports your machine."
)


def adb_state_remediation(state: object, raw_state: object = "") -> str:
    """The tester-facing next step for an adb state. ``""`` for ``device``.

    ONE producer for this text: the renderer must not invent a second wording,
    and a state with no entry gets the unknown text rather than silence. It is
    OUR prose, which is why the renderer keeps it OUTSIDE the untrusted block.

    ``raw_state`` is accepted and DELIBERATELY UNUSED. Selection keys off the
    bounded ``_ADB_KNOWN_STATES`` vocabulary alone, so a hostile device can
    choose WHICH canned sentence it sees but cannot write one. Wiring
    ``raw_state`` into the lookup or into the returned text would put
    device-supplied bytes inside the trusted region -- the untrusted-content
    finding this design already answered once. Do not resolve the unused
    argument by using it.
    """
    key = str(state or "")
    if key == "device":
        return ""
    return _ADB_REMEDIATION.get(key, _ADB_REMEDIATION_UNKNOWN)
