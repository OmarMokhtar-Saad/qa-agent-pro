"""The emulator-console side of the network capture: file name, uid, start, AVD path.

WHAT IT CANNOT DO, stated once here and again in the report. The traffic is TLS,
so a HOST is derivable and a URL PATH is not; and the capture is taken by the
EMULATOR CONSOLE, so a run on a physical device gets ``adb.NOT_AN_EMULATOR`` by
name and keeps its logcat evidence. The network capture lives beside the logcat
capture because both answer "what did the app do underneath this case", both are
bounded by the same flag, and both must be started between the force-stop and the
launch.
"""

from __future__ import annotations

import re
from typing import Any, cast

from tools.mobile import adb
from tools.mobile_evidence import capture_consts as _consts
from tools.mobile_evidence import netattr


def _capture_name(run_id: object, tc_id: object, index: object) -> str:
    """A BARE console file name built from this run. Never a path, by construction."""
    stem = re.sub(r"[^A-Za-z0-9]+", "-", str(run_id) + "-" + str(tc_id))[:56]
    stem = stem.strip("-") or "run"
    try:
        number = max(0, int(cast("Any", index) or 0))
    except (TypeError, ValueError, OverflowError):
        number = 0
    return _consts.NETWORK_NAME_PREFIX + stem + "-" + str(number) + ".pcap"


async def _app_uid(serial: str, package: str):
    """The app uid, or None. A uid nobody could read is not a uid nobody owns."""
    if not adb.valid_package_name(package):
        return None
    result = await adb.shell(serial, ["dumpsys", "package", str(package)])
    if result.get("error"):
        return None
    return netattr.app_uid(str((result.get("content") or {}).get("out") or ""))


async def _capture_start(serial: str, name: str) -> dict:
    """``emu network capture start <name>``, with the bare-name check FIRST.

    The console makes the same check and its refusal exits ZERO, so a name this
    tree built must be judged before it is handed over rather than after: the
    only thing that comes back from a refusal is text, and text is not a place
    to discover that a run id put a path separator in a file name.
    """
    if not adb.valid_capture_name(name):
        return {
            "error": (
                "Refusing capture file name "
                + repr(str(name)[:60])
                + "; the emulator console takes a bare file name ending in .pcap."
            ),
            "content": None,
        }
    return await adb.emu(serial, "network", "capture", "start", str(name))


async def _avd_path(serial: str) -> dict:
    """``emu avd path``: the AVD content directory. ``{"error", "content": str}``.

    The LAST line of the reply, not the first: the console prints its answer
    last, and ``adb.emu`` has already stripped the terminator. An empty reply is
    an error rather than an empty string, because the caller is about to build a
    file path out of it.
    """
    result = await adb.emu(serial, "avd", "path")
    if result.get("error"):
        return result
    lines = [
        str(line).strip() for line in (result.get("content") or []) if str(line).strip()
    ]
    if not lines:
        return {
            "error": "The emulator answered the avd path command with no path.",
            "content": None,
        }
    return {"error": None, "content": lines[-1]}
