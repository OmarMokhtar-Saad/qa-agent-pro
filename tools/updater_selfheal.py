"""Updater integrity pass: heal drifted files from the matching release (split from tools/updater.py)."""

from __future__ import annotations

from pathlib import Path

from tools.updater_heal import MANIFEST_HEAL_RETRY_INTERVAL_S, _manifest_heal_deferred
from tools.updater_lock import _update_lock
from tools.updater_log import logger
from tools.updater_manifest import integrity_problems, lock_files
from tools.updater_release import _Found, _heal_from_release, _release_of
from tools.updater_version import _parse_version


# Heal only when the latest release matches the installed version —
# that zipball is the exact tree the local MANIFEST.sha256 describes.
def _is_same_release(found: _Found) -> bool:
    """True when the release has an archive and equals the installed version."""
    remote = _parse_version(found.remote or "")
    return (
        bool(found.download_url)
        and remote is not None
        and remote == _parse_version(found.local or "")
    )


def _log_heal_deferred(remote: str) -> None:
    """Log that a manifest-only heal is deferred inside the retry floor."""
    logger.info(
        "Manifest heal for %s deferred: already tried within %d h.",
        remote,
        MANIFEST_HEAL_RETRY_INTERVAL_S // 3600,
    )


def _log_healing(mismatched: list, remote: str) -> None:
    """Log that modified files are being healed from the matching release."""
    logger.warning(
        "Integrity check: %d locally modified file(s) %s — healing from release %s.",
        len(mismatched),
        mismatched[:5],
        remote,
    )


def _log_cannot_heal(mismatched: list, local: object) -> None:
    """Log that modified files cannot be healed: no matching release archive."""
    logger.warning(
        "Integrity check: %d modified file(s) but no release zipball matching "
        "local version %s — cannot heal.",
        len(mismatched),
        local,
    )


# ops-5 (issue 5): only announce real work. A no-op pass logs at
# DEBUG, so "Code lock: N file(s)" now means N files ACTUALLY drifted
# and were re-locked -- worth noticing -- instead of appearing every
# 15 minutes and training the reader to ignore it.
def _relock_and_log(install_dir: Path) -> None:
    """Re-lock drifted files and log only when there was real work."""
    locked = lock_files(install_dir)
    if locked:
        logger.info("Code lock: %d file(s) re-locked read-only.", locked)
    else:
        logger.debug("Code lock: all files already read-only.")


# 2026-10-21 (E05): two launchers on one install both poll at the same
# minute. One applies the new release while the other hashes the tree
# half-overlaid and reports "29 locally modified files", then heals a tree
# that was never drifted. Hashing under the update lock makes that
# impossible: the check waits for no one (a held lock means an update is
# in flight, so there is nothing to judge) and never sees a partial tree.
def _problems_when_quiet(install_dir: Path) -> list | None:
    """Integrity problems, or None while another process is updating."""
    with _update_lock(install_dir) as acquired:
        if not acquired:
            logger.info("Integrity check skipped: an update is in flight.")
            return None
        return integrity_problems(install_dir)


# Integrity pass for an up-to-date install: heal from the matching
# release when files drifted, then re-lock. Returns the status string.
# integrity_problems, not verify_integrity: a deleted or emptied
# MANIFEST.sha256 must heal (same release, same signature and
# binding gates) rather than read as a clean tree.
def _lock_and_heal(install_dir: Path, found: _Found) -> str:
    """Heal drifted files from the matching release, then re-lock."""
    status = "up-to-date"
    rel = _release_of(found)
    mismatched = _problems_when_quiet(install_dir)
    if mismatched is None:
        return status  # peer mid-update: no chmod racing its swap
    same_release = _is_same_release(found)
    if same_release and _manifest_heal_deferred(install_dir, mismatched, found.remote):
        _log_heal_deferred(found.remote)
        mismatched = []
    if mismatched and same_release:
        _log_healing(mismatched, found.remote)
        status = _heal_from_release(install_dir, rel)
    elif mismatched:
        _log_cannot_heal(mismatched, found.local)
    _relock_and_log(install_dir)
    return status
