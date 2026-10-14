"""Archive safety: size ceilings, the traversal guard and the refusal replies.

The ceilings below have ONE home here; the tar and zip leaves read them as
attributes of this module so a patch on it bites both.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("tools.mobile_capture.mitm_provision")

#: Ceiling on the mitmdump ARCHIVE this module downloads (the compressed
#: bytes) and, separately, on any SINGLE member inside it. See the CEILINGS
#: row in ``tests/test_bounds_upper.py`` for the constraint that sizes this.
MAX_ARCHIVE_BYTES: int = 200 * 1024 * 1024

#: Ceiling on the SUM of every extracted file's bytes across the whole
#: archive. A many-small-members archive can walk straight past
#: ``MAX_ARCHIVE_BYTES`` (which only bounds one member at a time) while
#: staying under it on every individual file -- this is what closes that
#: gap. See the CEILINGS row in ``tests/test_bounds_upper.py``.
MAX_ARCHIVE_TOTAL_BYTES: int = 300 * 1024 * 1024

#: Ceiling on the NUMBER of members an archive may declare. A real mitmdump
#: release carries either 3 (Linux, flat) or 327 (macOS, the ``.app``
#: bundle) members. See the CEILINGS row in ``tests/test_bounds_upper.py``.
MAX_ARCHIVE_MEMBERS: int = 1000


class _TraversalRefused(Exception):
    """An archive member would resolve outside the destination directory.

    Raised internally by :func:`safe_join` and caught inside
    :func:`_extract_archive` -- it never escapes this module's public
    surface, which keeps the ``{"error", "content"}`` contract intact.
    """


def _archive_suffix(url: str) -> str:
    name = str(url or "").rsplit("/", 1)[-1]
    if name.lower().endswith(".tar.gz"):
        return ".tar.gz"
    if name.lower().endswith(".tgz"):
        return ".tgz"
    if name.lower().endswith(".zip"):
        return ".zip"
    return ".bin"


def safe_join(dest_dir: Path, member_name: str) -> Path:
    """Resolve *member_name* under *dest_dir*, refusing an escape.

    This is the FIRST of two independent traversal guards -- an explicit
    containment check run against every member before anything is written.
    The second is ``filter="data"`` on ``tarfile.extractall`` (PEP 706,
    Python 3.12+). Neither is trusted alone: the tests drive each with the
    other disabled.
    """
    dest_resolved = dest_dir.resolve()
    candidate = (dest_dir / member_name).resolve()
    try:
        candidate.relative_to(dest_resolved)
    except ValueError:
        raise _TraversalRefused(member_name) from None
    return candidate


def members_refusal(count: int) -> dict:
    """The refusal for an archive declaring *count* members (over the cap)."""
    return {
        "error": (
            "archive has "
            + str(count)
            + " members, over MAX_ARCHIVE_MEMBERS -- refusing"
        ),
        "content": None,
    }


def member_size_refusal(name: str, size: int) -> dict:
    """The refusal for one member of *size* bytes over MAX_ARCHIVE_BYTES."""
    return {
        "error": (
            name
            + " inside the archive is "
            + str(size)
            + " bytes, over MAX_ARCHIVE_BYTES -- refusing"
        ),
        "content": None,
    }


def total_refusal() -> dict:
    """The refusal for an archive whose extracted total is over the cap."""
    return {
        "error": (
            "the archive's extracted total exceeds MAX_ARCHIVE_TOTAL_BYTES -- refusing"
        ),
        "content": None,
    }
