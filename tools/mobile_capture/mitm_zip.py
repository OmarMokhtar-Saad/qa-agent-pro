"""Zip extraction: validate every member first, then write."""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

from tools.mobile_capture import mitm_safe

logger = logging.getLogger("tools.mobile_capture.mitm_provision")


def _extract_zip(archive_path: Path, dest_dir: Path) -> dict:
    with zipfile.ZipFile(archive_path) as zf:
        infos = zf.infolist()
        if len(infos) > mitm_safe.MAX_ARCHIVE_MEMBERS:
            return mitm_safe.members_refusal(len(infos))
        refused, targets = _extract_zip_validate(infos, dest_dir)
        if refused is not None:
            return refused
        # Every member validated (size, count, containment) before any byte
        # is written -- a rejected archive leaves nothing behind.
        refused = _extract_zip_write(zf, targets)
        if refused is not None:
            return refused
    return {"error": None, "content": {"dest_dir": str(dest_dir)}}


def _zip_member_refusal(info: zipfile.ZipInfo, total: int) -> dict | None:
    """The refusal for one file member, given the running *total* with it."""
    if info.file_size > mitm_safe.MAX_ARCHIVE_BYTES:
        return mitm_safe.member_size_refusal(info.filename, info.file_size)
    if total > mitm_safe.MAX_ARCHIVE_TOTAL_BYTES:
        return mitm_safe.total_refusal()
    return None


def _extract_zip_validate(
    infos: list[zipfile.ZipInfo], dest_dir: Path
) -> tuple[dict | None, list[tuple[zipfile.ZipInfo, Path]]]:
    """Check every member; return (refusal or None, the validated targets)."""
    targets: list[tuple[zipfile.ZipInfo, Path]] = []
    total = 0
    for info in infos:
        target = mitm_safe.safe_join(dest_dir, info.filename)
        if not info.is_dir():
            total += info.file_size
            refused = _zip_member_refusal(info, total)
            if refused is not None:
                return refused, targets
        targets.append((info, target))
    return None, targets


def _extract_zip_write(
    zf: zipfile.ZipFile, targets: list[tuple[zipfile.ZipInfo, Path]]
) -> dict | None:
    """Write the validated members; return a refusal if one is too big once read."""
    for info, target in targets:
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        data = zf.read(info)
        if len(data) > mitm_safe.MAX_ARCHIVE_BYTES:
            return {
                "error": (
                    info.filename + " exceeds MAX_ARCHIVE_BYTES once read -- refusing"
                ),
                "content": None,
            }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return None
