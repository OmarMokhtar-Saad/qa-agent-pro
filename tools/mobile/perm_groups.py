"""Permission groups for ``deny_permissions``: friendly name -> Android permissions.

Every runtime permission is granted by default; the test prompt may deny some. The
tester names a GROUP (``location``, ``camera``...) or a raw ``android.permission.*``
name. A group maps to the Android permissions revoked at setup; its keywords
(``perm_keywords``) let the pop-up handler tell which group a dialog is asking for.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

_P = "android.permission."

#: Group name -> the runtime permissions it covers (short names, ``_P`` prefixed below).
_GROUP_SHORT = {
    "location": "ACCESS_FINE_LOCATION ACCESS_COARSE_LOCATION ACCESS_BACKGROUND_LOCATION",
    "camera": "CAMERA",
    "microphone": "RECORD_AUDIO",
    "notifications": "POST_NOTIFICATIONS",
    "contacts": "READ_CONTACTS WRITE_CONTACTS GET_ACCOUNTS",
    "photos/storage": (
        "READ_EXTERNAL_STORAGE WRITE_EXTERNAL_STORAGE READ_MEDIA_IMAGES "
        "READ_MEDIA_VIDEO READ_MEDIA_AUDIO READ_MEDIA_VISUAL_USER_SELECTED "
        "ACCESS_MEDIA_LOCATION"
    ),
    "phone": (
        "READ_PHONE_STATE READ_PHONE_NUMBERS CALL_PHONE READ_CALL_LOG "
        "WRITE_CALL_LOG ANSWER_PHONE_CALLS ADD_VOICEMAIL USE_SIP"
    ),
    "sms": "SEND_SMS RECEIVE_SMS READ_SMS RECEIVE_WAP_PUSH RECEIVE_MMS",
    "calendar": "READ_CALENDAR WRITE_CALENDAR",
    "nearby/bluetooth": (
        "BLUETOOTH_CONNECT BLUETOOTH_SCAN BLUETOOTH_ADVERTISE "
        "NEARBY_WIFI_DEVICES UWB_RANGING"
    ),
    "body_sensors": "BODY_SENSORS BODY_SENSORS_BACKGROUND",
    "activity": "ACTIVITY_RECOGNITION",
}
GROUPS: dict[str, tuple[str, ...]] = {
    name: tuple(_P + short for short in shorts.split())
    for name, shorts in _GROUP_SHORT.items()
}
#: Other spellings a tester may use for a group.
ALIASES = {
    "photos": "photos/storage",
    "storage": "photos/storage",
    "photos_storage": "photos/storage",
    "media": "photos/storage",
    "nearby": "nearby/bluetooth",
    "bluetooth": "nearby/bluetooth",
    "nearby_devices": "nearby/bluetooth",
    "body": "body_sensors",
    "sensors": "body_sensors",
    "physical_activity": "activity",
}
_RAW_OWNER = {perm: name for name, perms in GROUPS.items() for perm in perms}


def valid_names() -> str:
    """The accepted friendly names, comma separated (for the error message)."""
    return ", ".join(GROUPS) + ", or a raw android.permission.* name"


def _canonical(value: object) -> str:
    """The group name or raw permission *value* stands for, or ``""``."""
    text = str(value or "").strip()
    if text.startswith(_P) and text[len(_P) :].replace("_", "").isalnum():
        return text
    key = text.lower().replace(" ", "_").replace("-", "_")
    return key if key in GROUPS else ALIASES.get(key, "")


def _listed(values: object) -> list[Any]:
    """*values* as a list: ``[]`` when it is not iterable."""
    return list(values) if isinstance(values, Iterable) else []


def _items(values: object) -> list[str]:
    """The comma-split, non-empty entries of a list (or one string)."""
    raw = [values] if isinstance(values, str) else _listed(values)
    return [p.strip() for v in raw for p in str(v).split(",") if p.strip()]


def parse_deny(values: object) -> tuple[list[str], str]:
    """``(canonical names, error)``; an unknown name gives an error listing the valid ones."""
    names: list[str] = []
    bad: list[str] = []
    for item in _items(values):
        name = _canonical(item)
        if not name:
            bad.append(item[:60])
        elif name not in names:
            names.append(name)
    if bad:
        return [], "unknown deny_permissions %s; valid: %s." % (bad, valid_names())
    return names, ""


def perms_of(names: object) -> list[str]:
    """The Android permission names the canonical *names* cover, in order, unique."""
    out: list[str] = []
    for name in _listed(names):
        for perm in GROUPS.get(name) or (name,):
            if perm not in out:
                out.append(perm)
    return out


def groups_of(names: object) -> frozenset:
    """The groups the canonical *names* belong to (a raw name via its owner group; one no group owns stays itself)."""
    return frozenset(
        name if name in GROUPS else _RAW_OWNER.get(name, name)
        for name in _listed(names)
    )
