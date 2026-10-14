"""``adb devices``: the serials that can be driven (moved from adb.py)."""

from __future__ import annotations

from tools.device_ids import parse_adb_devices
from tools.mobile.adb_host import host
from tools.mobile.adb_reply import rc_failure


def _devices_failed(body: dict, rc: int) -> dict:
    """The refusal for ``adb devices`` exiting non-zero, quoting adb's words."""
    return rc_failure("adb devices", body, rc)


def _devices_truncated(dropped_lines: int) -> dict:
    """THE SAME REFUSAL SHAPE AS A NON-ZERO rc, for the same reason. A
    truncated list cannot answer "which serials can I drive?": the one the
    caller wants may be a line that was never read, and every caller here
    spawns, adopts or reports on the answer -- `find_running` would start a
    SECOND emulator over the tester's own (D1, 2026-09-03). All four callers
    already branch on `error`, so the safe direction needs no second sentinel.
    """
    return {
        "error": (
            "adb listed more transports than this server reads ("
            + str(dropped_lines)
            + " line(s) past the limit were not parsed), so it cannot say "
            "which devices are connected. Unplug what you are not using, "
            "or run `adb kill-server`, then try again."
        ),
        "content": None,
    }


def _serials(result: dict) -> dict:
    # ONE PARSER, TWO READERS. This used to re-derive `parts[1] == "device"`
    # beside the identical line in `device_manager._list_android`, and both
    # dropped an `unauthorized` phone in silence. The classification now happens
    # once; this caller keeps its contract -- serials it can DRIVE, nothing else
    # -- because every one of its callers (emulator boot-wait, emulator adopt,
    # the qa-doctor lock probe) asks exactly that question. The states are
    # reported to the tester by `qa_list_devices`, which is where a person reads.
    rows = parse_adb_devices(str((result["content"] or {}).get("out") or ""))
    if rows.dropped_lines:
        return _devices_truncated(rows.dropped_lines)
    serials = [str(row["serial"]) for row in rows if row.get("usable")]
    return {"error": None, "content": serials}


async def devices() -> dict:
    """Serials of devices in the ``device`` state."""
    result = await host().raw(["devices"])
    if result.get("error"):
        return result
    # A NON-ZERO EXIT IS A FAILED PROBE, NOT AN EMPTY MACHINE. `raw` only sets
    # `error` when adb could not be run at all (spawn failure, timeout); a
    # command that ran and failed arrives here with error=None and rc!=0, and
    # its stdout carries no serials. Reporting that as an empty list told the
    # caller "nothing is booted", which is how a broken adb server ended up
    # spawning a second emulator over the tester's own (D1, 2026-09-03).
    body = result.get("content") or {}
    rc = int(body.get("rc") or 0)
    if rc != 0:
        return host()._devices_failed(body, rc)
    return _serials(result)
