"""Where the API-capture lane keeps its files.

This module INVENTS NO ROOT. Every path below is derived from
``tools.mobile.paths.sub("capture")``, so an operator who moves the mobile cache
with ``QA_MOBILE_CACHE_DIR`` moves the capture files with it, and there is
exactly one answer to "where does this live". A second root computed here would
be a second answer that drifts the first time the cache setting is read in only
one of the two places.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from tools.mobile import paths as mobile_paths

logger = logging.getLogger(__name__)

#: The sub-directory name. It is also listed in ``tools.mobile.paths.SUBDIRS``,
#: so the lane's own ``ensure_tree`` creates it alongside ``runs/`` and
#: ``state/`` -- one tree, made in one place.
CAPTURE_SUBDIR = "capture"

#: What a marker FILENAME may carry from a serial. Serials are vendor strings
#: (``emulator-5554``, ``R58M12ABCDE``), but a serial is device-supplied, and a
#: path separator in one would put the marker outside the capture directory --
#: where the startup reap can never find it, which is the one failure this lane
#: must not have. Substituted rather than refused: a marker we cannot NAME is a
#: proxy we cannot reap, so a odd serial still gets a file.
_UNSAFE_IN_NAME = re.compile(r"[^A-Za-z0-9._-]")
_DOT_RUN = re.compile(r"\.{2,}")


def capture_root() -> Path:
    """The capture directory. Creates nothing."""
    return mobile_paths.sub(CAPTURE_SUBDIR)


def ca_dir() -> Path:
    """Where our persistent root CA and its private key live."""
    return capture_root() / "ca"


def tmp_dir() -> Path:
    """Where the proxy addon's RAW JSONL lands, before redaction.

    Deliberately outside ``runs/``: the raw file is unredacted by construction,
    and the run directory is the thing a tester copies around and attaches to a
    ticket. Keeping the two apart is why redaction can be a write-time step
    rather than a promise about a directory.
    """
    return capture_root() / "tmp"


def ledger_path() -> Path:
    """The per-serial trust ledger. Outlives every run."""
    return capture_root() / "ledger.json"


def safe_name(serial: str) -> str:
    """*serial* reduced to characters legal in a filename on every platform."""
    # 64 characters: longer than any vendor serial seen on this lane, and short
    # enough to stay inside the shortest filename limit we ship to. Inline
    # rather than a module constant because it bounds a NAME, not a resource.
    cleaned = _UNSAFE_IN_NAME.sub("_", str(serial))
    # A dot is legal in a serial (a network-adb serial is an IP), but a RUN of
    # them is the traversal token. The separator substitution above already
    # flattens the path, so this is defence in depth on the NAME itself: a
    # marker whose name reads '..' is one a reader cannot trust at a glance,
    # and the startup reap globs these names.
    cleaned = _DOT_RUN.sub("_", cleaned)[:64]
    return cleaned or "unknown"


def proxy_marker(serial: str) -> Path:
    """The runtime marker for a live proxy on *serial*.

    NOT evidence. Its only job is to make the next run's startup reap possible
    after a crash, which is why it sits beside the ledger rather than inside a
    run directory that a crash may never finish writing.
    """
    return capture_root() / ("proxy-" + safe_name(serial) + ".json")


def cert_marker(serial: str) -> Path:
    """The local record of what ``cert.install`` last did for *serial*.

    Mirrors :func:`proxy_marker`'s own naming and safety discipline -- the
    same "a serial cannot smuggle a path separator" guard through
    :func:`safe_name`. Not evidence either: it exists so ``cert.status`` and
    ``cert.remove`` know which route and device path a prior install chose,
    without re-deriving it (a route re-derived twice is two answers to one
    question).
    """
    return capture_root() / ("cert-" + safe_name(serial) + ".json")


def ensure_tree() -> dict:
    """Create ``capture/`` and its children. ``{"error", "content"}``."""
    try:
        base = mobile_paths.ensure_tree()
        if base.get("error"):
            return {"error": base["error"], "content": None}
        dirs: dict[str, str] = {}
        for path in (capture_root(), ca_dir(), tmp_dir()):
            path.mkdir(parents=True, exist_ok=True)
            try:
                path.chmod(0o700)
            except OSError:
                # Windows ignores POSIX modes and some network volumes refuse
                # the call outright, exactly as mobile.paths.ensure_tree says.
                logger.info("mobile_capture.paths: could not tighten %s", path)
            dirs[path.name] = str(path)
        return {
            "error": None,
            "content": {"root": str(capture_root()), "dirs": dirs},
        }
    except Exception as exc:
        logger.exception("mobile_capture.paths.ensure_tree failed")
        return {"error": str(exc), "content": None}
