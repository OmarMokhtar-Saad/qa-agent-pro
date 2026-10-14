"""Reading the capture file out of the AVD directory, and deleting it.

THE RAW PCAP IS NEVER KEPT. It is written by the emulator into the AVD own
``console_out/``, read once, parsed, and DELETED, on every exit including the
failure path. What lands under the run folder is the parsed summary, which
carries host names and byte counts and no packet payload at all. A sweep removes
capture files this tree named that no live case can own.
"""

from __future__ import annotations

import logging
import os
import time

from tools.mobile import adb
from tools.mobile_evidence import capture_consts as _consts
from tools.mobile_evidence import pcap
from tools.mobile_evidence.capture_records import _network_skipped

logger = logging.getLogger("tools.mobile_evidence.capture")


def _pcap_path(avd_dir: object, name: object):
    """The capture file inside the AVD ``console_out``, or None.

    The name is re-checked HERE against the same bare-name rule the console
    enforces, so this join can never leave the directory even if the record it
    came from was hand-edited on disk.
    """
    directory = str(avd_dir or "").strip()
    if not directory or not adb.valid_capture_name(name):
        return None
    if not os.path.isdir(directory):
        return None
    return os.path.join(directory, "console_out", str(name))


def _sweepable(name: str, keep: object) -> bool:
    return (
        name != str(keep or "")
        and adb.valid_capture_name(name)
        and name.startswith(_consts.NETWORK_NAME_PREFIX)
    )


def _sweep_one(target: str, now: float) -> bool:
    try:
        if now - os.path.getmtime(target) <= _consts.ORPHAN_CAPTURE_TTL_S:
            return False
        os.remove(target)
    except OSError:
        return False
    logger.info(
        "mobile_evidence.capture: removed an abandoned capture (%s)",
        os.path.basename(target),
    )
    return True


def _sweep_orphan_captures(directory: object, keep: object, *, now: float) -> int:
    """Delete capture files no live case can own. Returns how many went.

    Only files this tree names -- the prefix is ours -- and only ones older than
    ``ORPHAN_CAPTURE_TTL_S``. *keep* is the file this call is about to read,
    which is never swept however the clock reads. Never raises: a sweep is
    housekeeping and may not cost a case its evidence.
    """
    removed = 0
    try:
        folder = os.path.join(str(directory or ""), "console_out")
        if not os.path.isdir(folder):
            return 0
        for name in sorted(os.listdir(folder)):
            if _sweepable(name, keep) and _sweep_one(os.path.join(folder, name), now):
                removed += 1
    except Exception:  # pragma: no cover - housekeeping never raises
        logger.exception("mobile_evidence.capture: the orphan sweep failed")
    return removed


def _read_and_delete(path: str):
    """``(bytes, error)``. The file is removed on EVERY exit, read or not.

    Deleting on the failure path too is the point: a capture this server could
    not read is still a packet dump of a tester device sitting in their AVD.
    """
    data = b""
    error = ""
    try:
        with open(path, "rb") as handle:
            data = handle.read(pcap.MAX_PCAP_BYTES + 1)
    except OSError as exc:
        error = "the capture file could not be read: " + str(exc)[:200]
    finally:
        try:
            os.remove(path)
        except OSError:
            logger.info("mobile_evidence.capture: the capture file was not removed")
    return data, error


def _locate_refusal(located: dict) -> dict:
    return _network_skipped(
        "the capture was taken but the AVD directory could not be "
        "located, so it could not be read: " + str(located["error"]),
        "locate",
    )


def _path_refusal() -> dict:
    return _network_skipped(
        "the capture file path could not be built from the AVD "
        "directory the emulator named",
        "locate",
    )


def _read_pcap(located: dict, file_name: object) -> tuple[bytes, dict | None]:
    """Read and delete the capture file: (bytes, None), or (b"", the skipped record to return)."""
    if located.get("error"):
        return b"", _locate_refusal(located)
    target = _pcap_path(located.get("content"), file_name)
    if target is None:
        return b"", _path_refusal()
    # The directory is open anyway, so this is the one place a capture left
    # by a chat that closed can be found without a call of its own.
    _sweep_orphan_captures(located.get("content"), file_name, now=time.time())
    data, read_error = _read_and_delete(target)
    if read_error:
        return b"", _network_skipped(read_error, "read")
    return data, None
