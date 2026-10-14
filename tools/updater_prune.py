"""Updater prune planning: REMOVED_PATHS parsing and prunable files (split from tools/updater.py)."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple, Optional

from tools.updater_log import logger
from tools.updater_paths import (
    _NEVER_PRUNE,
    _REMOVED_PATHS_NAME,
    _is_derived_artifact,
    _is_protected,
)


def _removed_entry(line: str) -> Optional[str]:
    """Normalised install-relative path for one REMOVED_PATHS line, else ``None``."""
    rel = line.split("#", 1)[0].strip()
    if not rel:
        return None
    if "\\" in rel or rel.startswith("/") or ":" in rel:
        logger.warning("REMOVED_PATHS: ignoring non-relative entry %r.", rel)
        return None
    parts = [p for p in rel.split("/") if p and p != "."]
    if not parts or ".." in parts:
        logger.warning("REMOVED_PATHS: ignoring traversing entry %r.", rel)
        return None
    return "/".join(parts)


# Parse ``<tree>/REMOVED_PATHS`` -- one install-relative POSIX path per
# line, blanks and ``#`` comments ignored.
#
# WHY IT EXISTS: ``_prunable``'s condition (c) -- "the install's OLD manifest
# lists it" -- is what makes the forward-looking prune safe, and it is also
# what makes it unable to reach a file STRANDED by a release predating the
# prune. Such a file was installed by an older release whose manifest listed
# it, and that manifest has since been overwritten, so no evidence of it
# survives on the client. This file is that evidence, shipped forward.
#
# It REPLACES condition (c) with a different safety property -- an explicit,
# human-authored, code-reviewed list inside a SIGNED release, rather than an
# inference from client state. The other guards are UNCHANGED and still
# apply to every entry: a listed path is deleted only when it is absent from
# the new tree, is not protected/derived/never-prune, and the release tree
# carried its own manifest.
#
# Lines that could escape the install root are DROPPED, not honoured:
# absolute paths, drive letters, backslashes, and any ``..`` component. A
# malformed line is a no-op, never a traversal. Never raises -- a missing or
# unreadable file yields an empty set, which simply means "prune nothing
# extra".
def load_removed_paths(tree: Path) -> set:
    """Parse ``<tree>/REMOVED_PATHS`` into a set of safe relative paths."""
    try:
        raw = (tree / _REMOVED_PATHS_NAME).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    entries = (_removed_entry(line) for line in raw.splitlines())
    return {e for e in entries if e is not None}


# Install files a PREVIOUS release installed that the NEW release dropped.
#
# Three conditions, all required, and (c) is the safety property:
# (a) the file is on disk in the install, (b) it is absent from the new
# release tree, and (c) the install's OLD ``MANIFEST.sha256`` lists it. (c) is
# what restricts deletion to files this updater itself installed, so a
# tester's own scratch file -- or anything under ``data/`` / ``corpus/`` --
# can never be a candidate whatever else is true of it.
#
# WHY: ``apply_update`` was a pure OVERLAY, so a module a release DELETED
# stayed on every installed machine forever. The MOTIVATING SYMPTOM, measured
# on the live install after it self-updated 1.60.3 -> 1.60.4: eight unlisted
# ``.py`` files in ``tools/`` -- ``comment_reconciler``, ``image_description``,
# ``jira_attachments``, ``requirement_analyzer``, ``test_plan_report``,
# ``token_meter``, ``web_search``, ``zephyr_exporter``. Several were deleted
# specifically so the capability could not be revived
# (docs/RETIRED_CAPABILITIES.md), which leaving them installed defeats -- and
# ``verify_integrity`` cannot see them either, because it only walks manifest
# ENTRIES.
#
# Condition (c) alone is FORWARD-LOOKING and could never reach those eight:
# they are absent from the CURRENT manifest, having been installed by an
# older release whose manifest was overwritten. ``removed`` is the second
# admission route that does reach them -- the release-shipped
# ``REMOVED_PATHS`` list (see ``load_removed_paths``), which substitutes an
# explicit signed declaration for the client-state inference of (c). Every
# OTHER guard applies identically to both routes: absent from the new tree,
# not protected/derived/never-prune, backed up before unlink. See
# operations/runbook.md -> *Files a release removes*.
#
# Honours ``_is_protected`` and ``_is_derived_artifact`` exactly as the copy
# loop does, so the two skip sets stay identical. Pure; never raises.
class PruneInput(NamedTuple):
    """The facts ``_prunable`` decides on (a parameter object)."""

    old_manifest: dict
    new_rels: set
    install_dir: Path
    removed: Optional[set] = None


def _prunable(inp: PruneInput) -> list:
    """Install files a PREVIOUS release installed that the NEW release dropped."""
    out = []
    for rel in sorted(set(inp.old_manifest) | set(inp.removed or ())):
        if rel in inp.new_rels or rel in _NEVER_PRUNE:
            continue
        if _is_protected(rel) or _is_derived_artifact(rel):
            continue
        target = inp.install_dir / rel
        if target.is_file() and not target.is_symlink():
            out.append(rel)
    return out
