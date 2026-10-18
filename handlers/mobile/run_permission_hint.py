"""A preview that lists the runtime permissions the app has not been given.

A first call without ``apply`` stops at a preview, and a run that then meets a
permission dialog ends as ``needs_model``. Naming the pending permissions and
the one flag that grants them lets the tester decide before the first step.
Read-only (one ``dumpsys package``), and silent when the device, the package or
the read is unavailable: a missing hint never changes a reply.
"""

from __future__ import annotations

from handlers.mobile.run_request import _MobileTestRequest
from tools.device_ids import valid_package_name
from tools.mobile import adb_grant

PREVIEW_MARK = "nothing was changed"
SHOWN = 4


def hint_text(perms: list[str]) -> str:
    """The one-line hint naming up to ``SHOWN`` of *perms* (short names)."""
    names = [p.rsplit(".", 1)[-1] for p in perms]
    more = ", ..." if len(names) > SHOWN else ""
    return (
        "\n\n%d runtime permissions not granted (%s%s): rerun "
        "without `grant_permissions=false` unless you are testing the "
        "permission prompt." % (len(names), ", ".join(names[:SHOWN]), more)
    )


async def permission_hint(serial: str, package: str) -> str:
    """The hint for *package* on *serial*, or ``""`` when nothing is known."""
    if not serial or not valid_package_name(package or ""):
        return ""
    result = await adb_grant.ungranted(serial, package)
    perms = result.get("content") or []
    return hint_text(perms) if perms and not result.get("error") else ""


async def with_hint(req: _MobileTestRequest, serial: str, reply: str) -> str:
    """*reply* plus the hint when it is a preview and the grant is not asked for."""
    if req.grant_permissions or PREVIEW_MARK not in reply.lower():
        return reply
    return reply + await permission_hint(serial, req.package or req.app)
