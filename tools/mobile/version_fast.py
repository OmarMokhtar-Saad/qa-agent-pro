"""The installed versionCode in one cheap call, with the slow dump as the fallback.

``dumpsys package <pkg>`` prints the whole package record and takes seconds on a
busy device; ``cmd package list packages --show-versioncode <pkg>`` prints one
line. Any doubt (no match, an error, an odd line) falls back to the dump, so
this can only make the answer faster, never different.
"""

from __future__ import annotations

import re
from typing import Awaitable, Callable, Optional

from tools.device_manager import valid_package_name
from tools.mobile import adb
from tools.mobile.app_info import AppVersion, ShellFn, read_version

_LINE_RE = re.compile(r"^package:(\S+)\s+versionCode:(\d+)\b", re.MULTILINE)

VersionReader = Callable[..., Awaitable[Optional[AppVersion]]]


def parse_listed_code(text: object, package: str) -> Optional[int]:
    """The versionCode ``cmd package list`` printed for exactly *package*."""
    for name, code in _LINE_RE.findall(str(text or "")):
        if name == package:
            return int(code)
    return None


async def read_version_fast(
    serial: str, package: str, *, shell: Optional[ShellFn] = None
) -> Optional[AppVersion]:
    """The installed version (code only when the cheap call answered)."""
    if not valid_package_name(package):
        return None
    run = shell or adb.shell
    argv = ["cmd", "package", "list", "packages", "--show-versioncode", str(package)]
    got = await run(serial, argv)
    code = None
    if not got.get("error"):
        code = parse_listed_code((got.get("content") or {}).get("out"), str(package))
    if code is not None:
        return AppVersion(version_code=code)
    return await read_version(serial, package, shell=shell)
