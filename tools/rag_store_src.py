"""Source-scoped superseded-entry removal for the RAG store."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from tools import rag_store_disk as _disk
from tools.rag_store_prune import _atomic_write

logger = logging.getLogger("tools.rag_store")


@dataclass(frozen=True)
class Supersede:
    """Which entries a source replace may drop."""

    source_key: str
    keep_ids: set
    last_keep: int


# The insertion-order floor for a source replace.
#
# PRE-WINDOW LOST-WRITE NARROWING (2026-08-09, review M4). The stat guard
# in _rewrite_without_source only catches appends made INSIDE the read window.
# A peer client whose same-source entries were appended BEFORE the stat is
# already inside ``entries``, matches ``source_key``, and is not in THIS
# caller's ``keep_ids`` -- so its fresh suite was deleted. The corpus is
# append-only JSONL, so file order IS insertion order, and nothing positioned
# AFTER this caller's own newest kept entry can be superseded work of this
# caller's: those rows are spared. No clock is involved, so ``added_at``'s
# 1-second resolution is irrelevant and legacy rows need no new field.
#
# This NARROWS the race; it does NOT close it, and the difference matters:
#
# * (i) with an EMPTY ``keep_ids`` there is no floor at all and the documented
#   "remove every entry for this source" contract applies unchanged;
# * (ii) a peer append that landed BEFORE this caller's newest kept row -- the
#   ordinary "the peer finalized first" ordering -- is STILL removed. Closing
#   that needs a per-entry writer identity the corpus does not carry, and is
#   tracked separately. tests/test_genquality_batch_c.py pins BOTH orderings,
#   so the limitation is a recorded decision rather than an accident.
#
# An advisory lockfile would help neither case: the peer's append happened
# before the call even opened the file.
#
# The floor is the position of the LAST entry this caller just wrote. -1 means
# "no floor" -- either the caller passed no keep_ids (the documented
# remove-everything contract) or none of them are in the file, in which case
# there is nothing to anchor against and the pre-fix behaviour stands.
def _last_keep_index(entries: list, keep_ids: set) -> int:
    last_keep = -1
    if keep_ids:
        for index, entry in enumerate(entries):
            if isinstance(entry, dict) and str(entry.get("id") or "") in keep_ids:
                last_keep = index
    return last_keep


def _is_superseded(index: int, entry, sup: Supersede) -> bool:
    """True when *entry* is this caller's own superseded copy for the source."""
    if not isinstance(entry, dict):
        return False
    md = entry.get("metadata")
    if not isinstance(md, dict):
        return False
    if str(md.get("source_key") or "") != sup.source_key:
        return False
    if str(entry.get("id") or "") in sup.keep_ids:
        return False
    # Appended AFTER this caller's newest kept entry -> another writer's
    # later work, never this caller's superseded copy.
    return not (sup.keep_ids and sup.last_keep >= 0 and index > sup.last_keep)


def _kept_entries(entries: list, sup: Supersede) -> list:
    return [e for i, e in enumerate(entries) if not _is_superseded(i, e, sup)]


def _unchanged(path: Path, before) -> bool:
    after = _disk._file_signature(path)
    if before is not None and after is not None and after != before:
        logger.warning(
            "rag_store: %s changed under a source replace -- keeping both "
            "copies rather than losing a concurrent write",
            path.name,
        )
        return False
    return True


# Atomically replace *path* with *keep*; False when the replace is abandoned.
#
# LOST-WRITE GUARD (review W1): this is a read-modify-write over a file another
# process may be APPENDING to (_save_entry_sync), and a plain rewrite would
# silently drop anything appended between the read and the replace. The file's
# (size, mtime_ns) is captured with the read and re-checked immediately before
# tmp.replace; if it moved, the replace is ABANDONED and False is returned.
# That trades a missed de-duplication (the next run removes it) against never
# losing another writer's entry. It does not make the pre-existing _prune_sync
# race disappear -- it only refuses to widen it.
def _rewrite_without_source(path: Path, keep: list, before) -> bool:
    return _atomic_write(path, keep, lambda: _unchanged(path, before))


# replace_source_entries (facade) notes:
#
# Remove a source's SUPERSEDED entries so a re-run replaces, not appends.
#
# The write half of the RAG loop was append-only, so re-generating the same
# ticket stored a second full copy of its cases. The corpus grew 571 -> 676
# docs in one day of re-runs and a later prepare saw 5/5 retrieved neighbours
# flagged as duplicate risk. QA_RAG_MAX_ENTRIES bounds the FILE, not the
# duplication -- it prunes the OLDEST entries, which is precisely the wrong
# end.
#
# ``keep_ids`` is the set of entry ids the caller has JUST written for this
# source; they are spared and everything else under the same key goes. Called
# with no keep_ids it removes every entry for the source, which is why
# handlers.gen_persist_suite calls it only AFTER a successful
# write and only with the fresh ids: an empty or wholly-unserializable suite
# must never delete the previous good one.
#
# An empty key is a no-op (0 removed) -- that is how the caller says "this
# suite has no source identity, just append".
#
# Returns {"error": None, "content": {"removed": int}} on success and
# {"error": str, "content": None} on failure. Never raises.
#
# Drop entries whose ``metadata.source_key`` is *source_key* and whose ``id``
# is NOT in *keep_ids*.
#
# Returns how many were removed -- 0 when the file is missing, unreadable, or
# holds nothing to remove. Atomic rewrite through a unique pid+uuid temp name,
# exactly the discipline _prune_sync uses, so two cross-session writers cannot
# clobber a shared temp; a failure is logged and NEVER raised, which leaves the
# corpus untouched and the caller keeps both copies -- the behaviour that
# predates this function.
#
# The lost-write guard lives in _rewrite_without_source (review W1) and the
# pre-window narrowing in _last_keep_index (review M4); read both before
# changing this function.
#
# It can only ever remove entries THIS code path stamped: ``source_key`` did
# not exist before 2026-08-09, so no legacy entry can match. Entries that are
# not dicts, or carry no metadata dict, are always kept.
#
# STAT FIRST, then read (review N1): taken after the load it would observe the
# state INCLUDING a concurrent append, so the re-check would compare
# post-append against post-append and always pass -- an inert guard that still
# dropped the line.
def _delete_source_sync(path: Path, source_key: str, keep_ids: set) -> int:
    try:
        if not path.is_file():
            return 0
        before = _disk._file_signature(path)
        entries = _disk.load_corpus_sync(path)
        sup = Supersede(source_key, keep_ids, _last_keep_index(entries, keep_ids))
        keep = _kept_entries(entries, sup)
        removed = len(entries) - len(keep)
        if removed <= 0:
            return 0
        if not _rewrite_without_source(path, keep, before):
            return 0
        return removed
    except Exception:
        logger.exception("rag_store: source replace failed for %s", path)
        return 0
