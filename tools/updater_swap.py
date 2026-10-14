"""Updater release swap with rollback (split from tools/updater.py)."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from tools.updater_log import logger
from tools.updater_manifest import load_manifest
from tools.updater_overlay import (
    _make_writable,
    _overlay_files,
    _plan_release_files,
    _prunable_for_tree,
    _prune_files,
    _Swap,
)


def _undo_created(swap: _Swap) -> None:
    """Delete files the swap newly created."""
    for rel in swap.created:
        try:
            (swap.install_dir / rel).unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover - defensive
            logger.warning("Rollback: could not remove new file %s (%s).", rel, exc)


def _undo_overwritten(swap: _Swap) -> None:
    """Restore files the swap overwrote from the backup."""
    for rel in swap.overwritten:
        backup = swap.backup_dir / rel
        if backup.exists():
            try:
                _make_writable(swap.install_dir / rel)
                shutil.copy2(backup, swap.install_dir / rel)
            except OSError as exc:  # pragma: no cover - defensive
                logger.warning("Rollback: could not restore %s (%s).", rel, exc)


def _undo_pruned(swap: _Swap) -> None:
    """Put back anything the prune pass removed."""
    for rel in swap.pruned:
        backup = swap.backup_dir / rel
        if backup.exists():
            try:
                (swap.install_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, swap.install_dir / rel)
            except OSError as exc:  # pragma: no cover - defensive
                logger.warning("Rollback: could not restore pruned %s (%s).", rel, exc)


# Undo a partial swap: delete newly-created files, restore overwritten ones
# from the backup, and put back anything the prune pass removed. Best-effort —
# individual failures are logged, not raised.
def _rollback(swap: _Swap) -> None:
    """Undo a partial swap (best-effort; failures are logged, not raised)."""
    _undo_created(swap)
    _undo_overwritten(swap)
    _undo_pruned(swap)


def _log_applied(swap: _Swap) -> None:
    """Log the counts of a finished swap."""
    logger.info(
        "Applied update: %d file(s) (%d new, %d replaced, %d pruned). Backup at %s",
        len(swap.created) + len(swap.overwritten),
        len(swap.created),
        len(swap.overwritten),
        len(swap.pruned),
        swap.backup_dir,
    )


def _swap_in(new_tree: Path, swap: _Swap) -> None:
    """Prune, then overlay, the release tree into the install."""
    plan = _plan_release_files(new_tree)
    # PRUNE FIRST, so a failure anywhere in the copy below rolls the
    # deletions back along with everything else -- a half-swapped install
    # must not also be missing modules. Each deletion is individually
    # guarded: one un-deletable orphan is logged and skipped, never allowed
    # to fail an otherwise good update.
    prunable = _prunable_for_tree(new_tree, plan, swap)
    _prune_files(prunable, swap)
    _overlay_files(plan, swap)
    _log_applied(swap)


# Swap the new release tree into the install, skipping every protected
# path. PRUNES files a previous release installed that this one dropped (see
# ``_prunable``), then overlays the new tree. Backs each pruned/replaced/
# created file up under ``backups/pre-update-*`` and rolls the whole swap back
# on any error (then re-raises). Returns the backup directory on success.
def apply_update(new_tree: Path, install_dir: Path, version: str = "update") -> Path:
    """Swap the release tree into the install; roll back and re-raise on error."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = install_dir / "backups" / f"pre-update-{version}-{ts}"
    # Read BEFORE anything is written: the swap below overwrites
    # MANIFEST.sha256 with the NEW release's copy, and the old listing is the
    # only record of what this updater put on disk.
    swap = _Swap(install_dir, backup_dir, load_manifest(install_dir), [], [], [])
    try:
        _swap_in(new_tree, swap)
        return backup_dir
    except Exception:
        logger.warning("Update swap failed mid-way — rolling back from backup.")
        _rollback(swap)
        raise
