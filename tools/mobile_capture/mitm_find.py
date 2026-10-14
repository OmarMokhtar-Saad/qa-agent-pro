"""Locate an existing ``mitmdump`` before any download is proposed.

**Locate before download.** :func:`find_mitmdump` checks PATH, then searches
``capture/mitm/`` for a previously-extracted binary -- mirroring
``tools/mobile/sdk_locator.py`` -- so a machine that already has mitmproxy
installed is never shadowed by a redundant fetch. Nothing downloads before
that check runs.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from tools.mobile_capture import mitm_pins

logger = logging.getLogger("tools.mobile_capture.mitm_provision")


def _resolve_binary(root: Path, leaf: str) -> Path | None:
    """Search the extracted tree for *leaf* by basename.

    The layout differs per platform -- macOS buries the executable in
    ``mitmproxy.app/Contents/MacOS/``, Linux ships it flat at the archive
    root -- so this makes neither shape special. The first match, in a
    deterministic (sorted) order, so a fixture with more than one candidate
    still resolves the same file on every run.
    """
    try:
        matches = sorted(p for p in root.rglob(leaf) if p.is_file())
    except OSError:
        return None
    return matches[0] if matches else None


def mitm_candidates() -> list[tuple[str, Path]]:
    """``(source label, path)`` in priority order. PATH first, ours last --
    the same "an existing install wins" priority ``sdk_locator`` uses."""
    out: list[tuple[str, Path]] = []
    on_path = shutil.which(mitm_pins.MITM_BIN)
    if on_path:
        out.append(("PATH", Path(on_path)))
    cached = _resolve_binary(mitm_pins.mitm_dir(), mitm_pins._leaf())
    if cached is not None:
        out.append(("qa-agents-cache", cached))
    return out


def _found_reply(source: str, candidate: Path) -> dict:
    """The ``found: True`` reply for *candidate*, labelled by *source*."""
    return {
        "error": None,
        "content": {"path": str(candidate), "source": source, "found": True},
    }


def _missing_reply() -> dict:
    """The ``found: False`` reply: no candidate exists."""
    return {
        "error": None,
        "content": {"path": "", "source": "", "found": False},
    }


def find_mitmdump() -> dict:
    """The best available ``mitmdump``, or ``found: False``. Never raises.

    Checked BEFORE :func:`provision` proposes any download -- the property
    ``test_an_existing_binary_on_path_is_used_and_nothing_is_downloaded``
    proves by spying on ``downloader.download`` rather than reading a log
    line.
    """
    try:
        for source, candidate in mitm_candidates():
            try:
                if candidate.is_file():
                    return _found_reply(source, candidate)
            except OSError:
                continue
        return _missing_reply()
    except Exception as exc:
        logger.exception("mobile_capture.mitm_provision.find_mitmdump failed")
        return {"error": str(exc), "content": None}
