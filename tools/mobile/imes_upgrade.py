"""Upgrade an older installed QA keyboard to the pinned build, once per device."""

from __future__ import annotations

import re

from tools.mobile import adb
from tools.mobile.ime_host import host
from tools.mobile.install_errors import UPDATE_INCOMPATIBLE

#: ``(serial, pinned version)`` pairs already judged in this server process.
#: Judged means: found current, upgraded, or refused. Either way the device is not
#: asked again, so a run never pays for the check twice and a refusal is said once.
_JUDGED: set[tuple[str, str]] = set()

_VERSION_RE = re.compile(r"versionName=(\S+)")


def reset() -> None:
    """Forget every verdict (tests, and a server that starts over)."""
    _JUDGED.clear()


def _key(version: str) -> tuple[int, ...] | None:
    """``1.2.0`` -> ``(1, 2, 0)``; ``None`` when it is not a plain dotted number."""
    parts = str(version or "").strip().split(".")
    if not parts or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


async def installed_version(serial: str, package: str) -> str:
    """``versionName`` of the installed package, ``""`` when it cannot be read."""
    got = await adb.shell(
        serial, ["dumpsys", "package", package], timeout=adb.PM_LIST_TIMEOUT_S
    )
    body = got.get("content") or {}
    if got.get("error") or body.get("rc") not in (0, None):
        return ""
    found = _VERSION_RE.search(str(body.get("out") or ""))
    return found.group(1) if found else ""


def _is_older(have: str, want: str) -> bool:
    """True only when both are plain versions and *have* is the lower one."""
    low, high = _key(have), _key(want)
    return low is not None and high is not None and low < high


async def older_version(serial: str, installed: bool) -> str:
    """The installed version when it is older than the pinned one, else ``""``.

    Asks the device at most once per device and pinned version in this process.
    Only OUR package is read: the package name comes from the pinned manifest.
    """
    info = host().manifest().get("content") or {}
    want = str(info.get("version") or "")
    token = (str(serial), want)
    if not installed or token in _JUDGED or not info.get("package"):
        return ""
    have = await installed_version(serial, str(info["package"]))
    if have:
        _JUDGED.add(token)
    return have if _is_older(have, want) else ""


def upgraded_note(old: str) -> str:
    """The user-visible line after a successful upgrade."""
    want = str((host().manifest().get("content") or {}).get("version") or "")
    return "QA keyboard upgraded (v" + old + "→v" + want + "); fast screen reading on"


def refused_note(old: str, failure: dict, serial: str) -> str:
    """Why the upgrade was not done, with the one-line fix for a key mismatch."""
    package = str((host().manifest().get("content") or {}).get("package") or "")
    why = str(failure.get("error") or "the install failed")[:200]
    note = "QA keyboard not upgraded (v" + old + "); fast screen reading stays off: "
    if failure.get("code") == UPDATE_INCOMPATIBLE:
        return (
            note
            + "it was signed with a different key. To fix it, uninstall it once "
            + "(this deletes its data): adb -s "
            + serial
            + " uninstall "
            + package
        )
    return note + why
