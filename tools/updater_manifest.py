"""Updater MANIFEST.sha256 parsing, integrity check and read-only lock (split from tools/updater.py)."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from tools.updater_log import logger
from tools.updater_paths import _LOCK_EXTRA, _MANIFEST_NAME, _is_protected

# Worker threads for the manifest hash pass in verify_integrity(). Hashing is
# disk-read plus hashlib (which drops the GIL on large buffers), so a handful
# of threads overlaps the I/O; more only adds seek contention.
_INTEGRITY_MAX_WORKERS = 8


def file_sha256(path: Path) -> Optional[str]:
    """Streaming SHA-256 hexdigest of a file; ``None`` when unreadable (an
    unreadable file counts as modified for integrity purposes)."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _parse_manifest_line(line: str) -> Optional[tuple]:
    """One manifest line -> ``(rel, digest)``; None when blank, comment or malformed."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = line.split(None, 1)
    if len(parts) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
        return None
    rel = parts[1].strip()
    if rel and ".." not in rel.split("/") and not _is_protected(rel):
        return rel, parts[0].lower()
    return None


def load_manifest(install_dir: Path) -> dict:
    """Parse ``MANIFEST.sha256`` (``<sha256-hex>  <rel/posix/path>`` per line)
    into ``{rel_path: hexdigest}``. A missing/unreadable manifest or malformed
    line degrades to an empty/partial dict — integrity checks become a no-op
    rather than an error. Protected paths are never accepted from a manifest."""
    manifest = install_dir / _MANIFEST_NAME
    entries: dict = {}
    try:
        if not manifest.is_file():
            return entries
        for line in manifest.read_text(encoding="utf-8").splitlines():
            parsed = _parse_manifest_line(line)
            if parsed:
                entries[parsed[0]] = parsed[1]
    except OSError as exc:
        logger.warning("Could not read %s (%s).", _MANIFEST_NAME, exc)
    return entries


def verify_integrity(install_dir: Path) -> list:
    """Return manifest-listed files whose on-disk content is missing or differs
    from the release hash (i.e. locally edited). Empty manifest -> ``[]``."""
    items = sorted(load_manifest(install_dir).items())
    if not items:
        return []
    # No hash cache: every start re-reads every file, which is the point of
    # the check. Only the serial loop became a bounded pool; the result is
    # still the sorted list of missing or differing paths.
    workers = min(_INTEGRITY_MAX_WORKERS, len(items))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        matches = list(
            pool.map(lambda item: file_sha256(install_dir / item[0]) == item[1], items)
        )
    return [rel for (rel, _digest), ok in zip(items, matches) if not ok]


def integrity_problems(install_dir: Path) -> list:
    """What a start-up GATE should heal: ``verify_integrity``'s list, plus
    ``[MANIFEST.sha256]`` when the manifest yields no usable entry at all.

    ``verify_integrity`` / ``load_manifest`` read a missing, empty or unparseable
    manifest as "nothing to check". That is right for the consumers that merely
    look (the drift watch, ``lock_files``, the prune pass) and wrong for a gate:
    deleting or emptying the manifest would switch the check off. A heal from the
    same-version release restores the file, so the state converges. Never
    raises beyond what ``verify_integrity`` does."""
    if not load_manifest(install_dir):
        return [_MANIFEST_NAME]
    return verify_integrity(install_dir)


# Shell scripts get read+exec unconditionally: zip extraction (updates/heals)
# drops POSIX modes, so start.sh would arrive 0o644 and a bare exec-bit-preserve
# would leave it unrunnable. Everything else: read-only, keeping any existing exec
# bits. The .sh exec-bit rule is POSIX-only. Windows synthesizes the mode from the
# read-only ATTRIBUTE, so a locked .sh reports 0o444 and could never equal 0o555 --
# the "mode is wrong" guard would miss every time and re-chmod the shell scripts on
# every 15-minute pass, logging each as if it were work.
def _desired_mode(rel: str, st_mode: int) -> int:
    """The read-only mode a locked file ``rel`` should have."""
    if rel.endswith(".sh") and os.name != "nt":
        return 0o555
    return 0o444 | (st_mode & 0o111)


# ops-5 (issue 5): chmod ONLY when the mode is actually wrong. This ran
# unconditionally on every update check -- 59 chmods every 15 minutes on an
# install that was already locked, each one logged at INFO as if it were work.
# The guard is deliberately "this file is not in the desired mode", NOT "the
# version looks unchanged": the lock is a security control, so a file that
# drifted writable (editor, stray chmod, partial extraction) must still be
# re-locked on the very next pass.
def lock_files(install_dir: Path) -> int:
    """chmod every manifest-listed code file (plus manifest/launcher/VERSION)
    read-only (0o444) so manual or editor saves fail with a permission error.
    Best-effort per file (failures logged, never raised); returns the count of
    files locked."""
    locked = 0
    targets = list(load_manifest(install_dir).keys()) + list(_LOCK_EXTRA)
    for rel in dict.fromkeys(targets):
        path = install_dir / rel
        try:
            if path.is_file():
                st_mode = os.stat(path).st_mode
                desired = _desired_mode(rel, st_mode)
                if stat.S_IMODE(st_mode) != desired:
                    os.chmod(path, desired)
                    locked += 1
        except OSError as exc:
            logger.warning("Could not lock %s (%s).", rel, exc)
    return locked


def _file_sha256(path: Path) -> Optional[str]:
    """Legacy name of :func:`file_sha256` (WX removes it)."""
    return file_sha256(path)
