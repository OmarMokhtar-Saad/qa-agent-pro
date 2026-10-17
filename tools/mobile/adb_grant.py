"""Opt-in runtime-permission pre-grant: ``grant_runtime`` (F7)."""

from __future__ import annotations

import re

from tools.device_ids import SYSTEM_PACKAGE_PREFIXES, valid_package_name
from tools.mobile.adb_host import host

_PERM_RE = re.compile(r"^\s+([A-Za-z0-9_.]{1,200}): granted=(true|false)(.*)$")
# A fixed permission is policy or the user's own "don't ask again": granting
# it would override a decision this tool does not own, so it is left alone.
_FIXED = ("USER_FIXED", "POLICY_FIXED", "SYSTEM_FIXED")
_FAIL = "qa-grant-failed:"
# The parsed block is user 0's, so the grant names user 0 too.
_USER = ["--user", "0"]
# Health Connect grants are health-data access, not a dialog a run meets:
# pre-granting them would hand the app data the user never chose to share.
_SKIP_PREFIXES = ("android.permission.health.",)


def _runtime_block(dump: str) -> list[str]:
    """The lines of the FIRST ``runtime permissions:`` block (user 0)."""
    lines = dump.splitlines()
    starts = [
        i for i, line in enumerate(lines) if line.strip() == "runtime permissions:"
    ]
    if not starts:
        return []
    block = []
    for line in lines[starts[0] + 1 :]:
        if not _PERM_RE.match(line):
            break
        block.append(line)
    return block


def ungranted_runtime(dump: str) -> list[str]:
    """Runtime permissions ``dumpsys package`` lists as not granted, not fixed."""
    out = []
    for line in _runtime_block(dump):
        match = _PERM_RE.match(line)
        if (
            match
            and match.group(2) == "false"
            and not any(flag in match.group(3) for flag in _FIXED)
            and not match.group(1).startswith(_SKIP_PREFIXES)
        ):
            out.append(match.group(1))
    return out


def _grant_argv(package: str, perms: list[str]) -> list[str]:
    """One shell line: every grant, each failure echoed by name, never aborting."""
    argv: list[str] = []
    for perm in perms:
        argv += ["pm", "grant", *_USER, package, perm]
        argv += ["2>/dev/null", "||", "echo", _FAIL + perm, ";"]
    return argv


def _failed(out: str) -> list[str]:
    return [
        line.strip()[len(_FAIL) :]
        for line in out.splitlines()
        if line.strip().startswith(_FAIL)
    ]


async def ungranted(serial: str, package: str) -> dict:
    result = await host().shell(serial, ["dumpsys", "package", package])
    body = result.get("content") or {}
    if result.get("error") or int(body.get("rc") or 0) != 0:
        return {"error": "could not read permissions of " + package, "content": None}
    return {"error": None, "content": ungranted_runtime(str(body.get("out") or ""))}


# ONE dumpsys plus ONE shell line, whatever the count: a grant costs ~60 ms
# on device, and a second case of the same run finds everything granted and
# sends no grant at all. A permission the device refuses (a Health Connect
# grant on an older OS, a hard-restricted one) is reported, never fatal.
async def grant_runtime(serial: str, package: str) -> dict:
    """Grant every not-yet-granted runtime permission *package* requests."""
    if not valid_package_name(package) or package.startswith(SYSTEM_PACKAGE_PREFIXES):
        return {"error": "Refusing to grant for " + repr(package[:60]), "content": None}
    pending = await ungranted(serial, package)
    perms = pending.get("content") or []
    if pending.get("error") or not perms:
        return {"error": pending.get("error"), "content": {"granted": [], "failed": []}}
    result = await host().shell(serial, _grant_argv(package, perms))
    if result.get("error"):
        return result
    failed = _failed(str((result.get("content") or {}).get("out") or ""))
    granted = [p for p in perms if p not in failed]
    return {"error": None, "content": {"granted": granted, "failed": failed}}
