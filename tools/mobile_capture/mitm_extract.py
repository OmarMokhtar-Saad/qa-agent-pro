"""Whole-archive extraction, dispatched on the archive suffix.

**The vendor archive is a whole bundle, not one file.** A real
``mitmdump`` binary is PyInstaller-frozen: on macOS it sits inside
``mitmproxy.app/Contents/MacOS/mitmdump`` and needs its sibling
``Python.framework`` and ``.so``/``.dylib`` files beside it to start at all
-- pulling the executable out by basename alone ships a binary that exits
255 with "Failed to load Python shared library". This module therefore
extracts the WHOLE archive into a per-version directory
(``capture/mitm/<version>/``) and then RESOLVES the binary's path inside it
by searching for its basename, rather than assuming one flat layout. Linux's
own release is a flat 3-member tarball with ``mitmdump`` at the archive
root -- the shipped single-member extraction happened to work there, which
is why a whole-archive design is still graded against BOTH real shapes (see
the test module).
"""

from __future__ import annotations

import logging
from pathlib import Path

from tools.mobile_capture import mitm_safe, mitm_tar, mitm_zip

logger = logging.getLogger("tools.mobile_capture.mitm_provision")


def _dispatch(archive_path: Path, dest_dir: Path) -> dict:
    """Extract by suffix, or refuse an unrecognised archive format."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    name_lower = archive_path.name.lower()
    if name_lower.endswith((".tar.gz", ".tgz")):
        return mitm_tar._extract_tar(archive_path, dest_dir)
    if name_lower.endswith(".zip"):
        return mitm_zip._extract_zip(archive_path, dest_dir)
    return {
        "error": "unrecognised archive format: " + archive_path.name,
        "content": None,
    }


def _traversal_reply(exc: Exception) -> dict:
    """The refusal for a member path that escapes the destination."""
    return {
        "error": (
            "refusing a member path that escapes the destination directory: " + str(exc)
        ),
        "content": None,
    }


def _extract_archive(archive_path: Path, dest_dir: Path) -> dict:
    """Extract the WHOLE *archive_path* into *dest_dir* -- never a single
    member.

    A PyInstaller-bundled ``mitmdump`` needs its sibling
    ``Python.framework``/``.so`` files beside the executable to start at
    all; pulling one member out by basename ships a binary that exits 255
    with "Failed to load Python shared library". Bounded by
    ``MAX_ARCHIVE_MEMBERS`` (member count), ``MAX_ARCHIVE_BYTES`` (each
    member) and ``MAX_ARCHIVE_TOTAL_BYTES`` (the sum of every extracted
    file) -- an archive of many small members can no longer walk past the
    per-member cap the way single-member extraction used to allow.
    """
    try:
        return _dispatch(archive_path, dest_dir)
    except mitm_safe._TraversalRefused as exc:
        return _traversal_reply(exc)
    except Exception as exc:
        logger.exception("mobile_capture.mitm_provision: extraction failed")
        return {"error": str(exc), "content": None}
