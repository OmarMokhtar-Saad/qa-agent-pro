"""Tar extraction with the member, size and traversal guards."""

from __future__ import annotations

import logging
import tarfile
from pathlib import Path

from tools.mobile_capture import mitm_safe

logger = logging.getLogger("tools.mobile_capture.mitm_provision")


def _tar_member_refusal(
    member: tarfile.TarInfo, dest_dir: Path, total: int
) -> tuple[dict | None, int]:
    """Check one member; return (refusal or None, the running file total)."""
    mitm_safe.safe_join(dest_dir, member.name)
    if not member.isfile():
        return None, total
    if member.size > mitm_safe.MAX_ARCHIVE_BYTES:
        return mitm_safe.member_size_refusal(member.name, member.size), total
    total += member.size
    if total > mitm_safe.MAX_ARCHIVE_TOTAL_BYTES:
        return mitm_safe.total_refusal(), total
    return None, total


def _extract_tar(archive_path: Path, dest_dir: Path) -> dict:
    with tarfile.open(archive_path, "r:gz") as tf:
        members = tf.getmembers()
        if len(members) > mitm_safe.MAX_ARCHIVE_MEMBERS:
            return mitm_safe.members_refusal(len(members))
        total = 0
        for member in members:
            refused, total = _tar_member_refusal(member, dest_dir, total)
            if refused is not None:
                return refused
        # Second traversal guard, independent of `safe_join` above -- see
        # its docstring.
        tf.extractall(dest_dir, filter="data")
    return {"error": None, "content": {"dest_dir": str(dest_dir)}}
