"""Updater swap planning and file overlay with backup (split from tools/updater.py)."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path
from typing import NamedTuple

from tools.updater_log import logger
from tools.updater_paths import (
    _MANIFEST_NAME,
    _is_derived_artifact,
    _is_protected,
)
from tools.updater_prune import PruneInput, _prunable, load_removed_paths


class _Swap(NamedTuple):
    """State of one swap; the lists are appended in place so a rollback sees partial progress."""

    install_dir: Path
    backup_dir: Path
    old_manifest: dict
    overwritten: list
    created: list
    pruned: list


def _make_writable(path: Path) -> None:
    """Clear the read-only bit ahead of an overwrite/restore. Locked installs
    (QA_CODE_LOCK_ENABLED) keep code files at 0o444; the updater itself must
    still be able to swap them. Never raises."""
    try:
        if path.exists():
            os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)
    except OSError as exc:
        logger.warning("Could not make %s writable (%s).", path, exc)


def _plan_release_files(new_tree: Path) -> list:
    """(source, relative posix path) for every file the swap may install."""
    plan = []
    for src in sorted(new_tree.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(new_tree).as_posix()
        if _is_protected(rel) or _is_derived_artifact(rel):
            continue
        plan.append((src, rel))
    return plan


def _prune_files(prunable: list, swap: _Swap) -> None:
    """Back up and delete each prunable file, appending to ``swap.pruned``."""
    # One un-deletable orphan is logged and skipped.
    for rel in prunable:
        dest = swap.install_dir / rel
        try:
            bdest = swap.backup_dir / rel
            bdest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, bdest)
            _make_writable(dest)
            dest.unlink()
        except OSError as exc:
            logger.warning("Could not prune %s (%s).", rel, exc)
            continue
        swap.pruned.append(rel)


# RETAINED, and not redundant now that _safe_extract_zip
# restores modes. That restore can only replay bits the archive
# CARRIED: a zip built on Windows has create_system != 3 and no
# POSIX bits at all, so `external_attr >> 16` is 0 and there is
# nothing to put back. This suffix rule is the only thing
# covering that case, and without it a shell script would land
# non-executable for the window between this copy and the later
# lock_files() pass (which only runs after _pip_install() /
# migrate_env() finish) -- long enough for a client's spawn
# attempt to hit EACCES.
def _exec_shell_script(dest: Path) -> None:
    """Mark the shell script at *dest* user/group/other executable."""
    st_mode = os.stat(dest).st_mode
    os.chmod(dest, st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _overlay_one(src: Path, rel: str, swap: _Swap) -> None:
    """Copy one planned file into the install, backing up a replaced one."""
    dest = swap.install_dir / rel
    if dest.exists():
        bdest = swap.backup_dir / rel
        bdest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dest, bdest)
        swap.overwritten.append(rel)
    else:
        swap.created.append(rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _make_writable(dest)
    shutil.copy2(src, dest)
    if rel.endswith(".sh"):
        _exec_shell_script(dest)


def _overlay_files(plan: list, swap: _Swap) -> None:
    """Copy each planned file into the install, recording into *swap* for rollback."""
    for src, rel in plan:
        _overlay_one(src, rel, swap)


# The paths to prune, but ONLY against a release tree with its own manifest.
# `new_rels` is the evidence that decides what gets DELETED, so an
# incomplete or mis-rooted `new_tree` does not merely under-copy, it
# over-deletes: every path in the old manifest that the tree happens
# not to contain satisfies all three of _prunable's conditions.
# _manifest_binding_ok has exactly ONE permissive branch -- a tree with
# no MANIFEST.sha256, waved through to "preserve current behavior" --
# and that permission was written when the current behaviour was a pure
# OVERLAY, which is non-destructive by construction. Prune changes what
# it grants, so it does not inherit it. Note the two hazards coincide:
# download_and_extract picks its root by counting DIRECTORIES only, so
# a mis-rooted tree also has no manifest at its root and is caught here.
def _prunable_for_tree(new_tree: Path, plan: list, swap: _Swap) -> list:
    """The paths to prune, only against a release tree with its own manifest."""
    if not (new_tree / _MANIFEST_NAME).is_file():
        logger.warning(
            "Release tree has no %s -- skipping the prune pass (the copy "
            "still runs). Nothing is deleted on an unvalidated tree.",
            _MANIFEST_NAME,
        )
        return []
    new_rels = {rel for _src, rel in plan}
    removed = load_removed_paths(new_tree)
    return _prunable(PruneInput(swap.old_manifest, new_rels, swap.install_dir, removed))
