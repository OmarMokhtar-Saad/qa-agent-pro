"""The disk side of the downloader: headroom, free-space check, digest, progress file."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

from tools.mobile import paths
from tools.mobile.downloader_host import host

logger = logging.getLogger("tools.mobile.downloader")

#: Read size. Also the digest block size.
CHUNK = 256 * 1024


def _gb(value: float) -> str:
    return "%.2f GB" % (float(value) / (1024.0**3))


def required_bytes(payload_bytes: int) -> int:
    """Disk space demanded for a *payload_bytes* download, headroom included."""
    payload = max(0, int(payload_bytes))
    if payload == 0:
        return 0
    factor, floor = host().DISK_HEADROOM_FACTOR, host().DISK_HEADROOM_FLOOR
    return int(max(payload * factor, payload + floor))


def _disk_row(ok: bool, detail: str, sizes: tuple[int, int]) -> dict:
    free, need = sizes
    return {
        "error": None,
        "content": {"ok": ok, "detail": detail, "free": free, "need": need},
    }


def _unknown_free(sizes: tuple[int, int]) -> dict:
    detail = (
        "Could not determine free space on the volume holding "
        "the mobile cache, so a " + _gb(sizes[1]) + " download is not started."
    )
    return _disk_row(False, detail, sizes)


def _disk_detail(payload_bytes: int, sizes: tuple[int, int]) -> str:
    free, need = sizes
    detail = (
        _gb(free)
        + " free, "
        + _gb(need)
        + " required (payload "
        + _gb(payload_bytes)
        + " plus unpack headroom)"
    )
    if free >= need:
        return detail
    return (
        "Not enough disk space: "
        + detail
        + ". Free at least "
        + _gb(need - free)
        + " and try again."
    )


# ``{ok, detail, free, need}`` for a planned download.
#
# An undeterminable free-space reading is reported as NOT ok: "unknown" must
# never read as "plenty", because the failure it hides is a half-written SDK.
def check_disk(payload_bytes: int, target: Path | None = None) -> dict:
    try:
        need = required_bytes(payload_bytes)
        free = paths.free_bytes(target)
        if free < 0:
            return _unknown_free((free, need))
        detail = _disk_detail(payload_bytes, (free, need))
        return _disk_row(free >= need, detail, (free, need))
    except Exception as exc:
        logger.exception("mobile.downloader.check_disk failed")
        return {"error": str(exc), "content": None}


def digest_file(path: Path) -> str:
    """Lower-case SHA-256 hex of *path*, read in chunks."""
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(CHUNK)
            if not block:
                break
            sha.update(block)
    return sha.hexdigest()


def write_progress(progress_path: str | Path | None, payload: dict) -> None:
    """Atomically write a progress JSON file. Failure is logged, never raised."""
    if not progress_path:
        return
    try:
        target = Path(progress_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(tmp, target)
    except (OSError, TypeError, ValueError):
        logger.info("mobile.downloader: could not write progress to %s", progress_path)


def _disk_refusal(payload_bytes: int, dest_path: Path) -> str:
    """Return the refusal text when the disk is too full, else ``""``."""
    disk = (host().check_disk(payload_bytes, dest_path.parent) or {}).get(
        "content"
    ) or {}
    if not disk.get("ok", False) and required_bytes(payload_bytes) > 0:
        return str(disk.get("detail") or "insufficient disk space")
    return ""
