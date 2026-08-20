"""Content fingerprint of the modules that decide what a prep CONTAINS.

F4 (2026-08-15). Host-mode generation is split across two stateless MCP calls,
and the prepared context persists between them in ``data/suites.db`` -- a file
EVERY server process on this machine shares. A prep built by pre-fix code is
therefore reused, unchanged, by a fixed server: the tester restarts the MCP
server, submits against a ``prep_id`` staged minutes earlier, and gets pre-fix
output with nothing saying so. Observed on the SHYJ-5645 validation run, where a
prep built before the _strip_html fix (F1) kept feeding placeholder-stripped
ticket text to a server that had been fixed.

``meta.app_version`` cannot see this. It is the installed VERSION string, and a
developer checkout reports the same ``0.1.0`` across every code change, so
``_version_skew_note`` -- the existing disclosure this one deliberately mirrors
and reuses -- stays silent through exactly the edits most likely to matter. A
content hash moves whenever the code does.

SCOPE, deliberately narrow: the modules that build the prepared context, ground
it, serialize it into the envelope and validate what comes back. A change to an
exporter or to a mobile module cannot make a staged prep stale, and including
those files would fire the warning on edits that change nothing about the prep.

Design constraints, in order:
  * NEVER raises. A fingerprint is disclosure; a missing one is silence, which
    is exactly today's behaviour, so every failure path returns "".
  * Computed ONCE per process and cached. It is read on the prepare path and on
    every submit; re-hashing ~1 MB of source per call would be pure waste.
  * CONTENT, not mtime or size. A git checkout on a second install has its own
    mtimes, and this must compare two INSTALLS as readily as two builds of one.
  * Stable across platforms: files are read as BYTES and newlines normalised, so
    a checkout with CRLF endings does not read as a different build.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# The install root: this file is <root>/tools/build_fingerprint.py.
_ROOT = Path(__file__).resolve().parent.parent

# Sorted so the hash never depends on the literal's order here.
_TRACKED: tuple[str, ...] = (
    "agents/host_mode.py",
    "agents/test_scenario_agent.py",
    "tools/atomic_checklist.py",
    "tools/jira_mcp.py",
    "tools/mcp_handlers.py",
    "tools/prep_store.py",
    "tools/rtm.py",
)

_CACHED: str | None = None


def _hash_file(path: Path) -> str:
    """sha256 of one file's newline-normalised bytes, or "" when unreadable.

    A file that is missing (a distribution build ships a whitelist subset, so
    several of the tracked modules legitimately are not there) contributes ""
    rather than aborting the whole fingerprint -- the same set of files is
    absent on every call within that install, so the value stays stable and
    comparable against another prep from the SAME install.
    """
    try:
        data = path.read_bytes().replace(b"\r\n", b"\n")
    except OSError:
        return ""
    return hashlib.sha256(data).hexdigest()


def code_fingerprint() -> str:
    """A short, stable hash of the prep-shaping modules. "" when unavailable.

    Cached for the life of the process, which is also the correct semantics:
    Python does not hot-reload, so the code THIS process is running cannot
    change under it. A running server that is updated on disk keeps reporting
    the build it actually executes, which is the honest answer -- the on-disk
    drift is a separate signal that ``_update_pending_note`` already carries.
    """
    global _CACHED
    if _CACHED is not None:
        return _CACHED
    try:
        digest = hashlib.sha256()
        for rel in sorted(_TRACKED):
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            digest.update(_hash_file(_ROOT / rel).encode("ascii"))
            digest.update(b"\0")
        _CACHED = digest.hexdigest()[:12]
    except Exception:  # pragma: no cover - defensive; disclosure must not raise
        logger.debug("code fingerprint unavailable", exc_info=True)
        _CACHED = ""
    return _CACHED
