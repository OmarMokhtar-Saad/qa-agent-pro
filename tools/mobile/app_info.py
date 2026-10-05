"""Installed app version: what is on the device right now.

``read_version`` parses ``dumpsys package <pkg>``. Only the ``Packages:``
section is read, so a ``versionCode=`` printed by another section never counts.
A package that is not installed, or an adb failure, is ``None`` -- never a
guessed version. Used by ``qa_app_info``, the App Tester flow (did the version
rise?) and the update verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from tools.device_manager import valid_package_name
from tools.mobile import adb

ShellFn = Callable[..., Awaitable[dict]]

_CODE_RE = re.compile(r"\bversionCode=(\d+)")
_NAME_RE = re.compile(r"\bversionName=(\S+)")
_UPDATED_RE = re.compile(r"\blastUpdateTime=(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_PACKAGES_MARK = "Packages:"


@dataclass(frozen=True)
class AppVersion:
    version_name: str = ""
    version_code: Optional[int] = None
    last_update: str = ""

    def to_dict(self) -> dict:
        return {
            "versionName": self.version_name,
            "versionCode": self.version_code,
            "lastUpdate": self.last_update,
        }

    def line(self) -> str:
        """One status line, for the run status and the tool result."""
        name = self.version_name or "unknown"
        code = "unknown" if self.version_code is None else str(self.version_code)
        updated = ", updated " + self.last_update if self.last_update else ""
        return "versionName " + name + ", versionCode " + code + updated


def parse_version(text: object) -> Optional[AppVersion]:
    """The version in a ``dumpsys package`` dump, or None when it names none."""
    body = str(text or "").split(_PACKAGES_MARK, 1)[-1]
    code = _CODE_RE.search(body)
    if code is None:
        return None
    name = _NAME_RE.search(body)
    updated = _UPDATED_RE.search(body)
    return AppVersion(
        name.group(1)[:60] if name else "",
        int(code.group(1)),
        updated.group(1) if updated else "",
    )


async def read_version(
    serial: str, package: str, *, shell: Optional[ShellFn] = None
) -> Optional[AppVersion]:
    """The installed version of ``package`` on ``serial``, or None."""
    if not valid_package_name(package):
        return None
    got = await (shell or adb.shell)(serial, ["dumpsys", "package", str(package)])
    if got.get("error"):
        return None
    return parse_version((got.get("content") or {}).get("out"))
