"""Atomic JSONL rewrite and cap pruning for the RAG store."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import uuid
from collections.abc import Callable
from pathlib import Path

from tools import rag_store_disk as _disk

logger = logging.getLogger("tools.rag_store")


def _unlink_quiet(tmp: Path) -> None:
    if tmp.exists():
        with contextlib.suppress(OSError):
            tmp.unlink()


# Write *rows* to a unique per-call temp name (+ pid) so two cross-session
# writers can't clobber a shared ".jsonl.tmp" and lose entries, then replace
# *path*. *ok*, when given, is checked after the temp write and right before
# the replace; a False abandons the replace and returns False. A crashed
# write must not leave its unique temp orphan behind.
def _atomic_write(path: Path, rows: list, ok: Callable[[], bool] | None = None) -> bool:
    tmp = path.with_suffix(f".{os.getpid()}.{uuid.uuid4().hex}.jsonl.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            for e in rows:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        if ok is not None and not ok():
            return False
        tmp.replace(path)
    finally:
        _unlink_quiet(tmp)
    return True


# Keep only the newest ``cap`` entries of a JSONL corpus file (append order is
# chronological). Atomic rewrite; failures are logged, never raised -- a failed
# prune just leaves the corpus slightly over cap.
#
# Cheap gate FIRST: a newline count, not a full parse. Without this the
# function parsed the entire corpus on every add merely to discover it had
# nothing to do.
#
# The gate is EXACTLY the cap, deliberately. An earlier version of this fix
# added slack so a corpus sitting AT the cap would not rewrite on every
# add -- but any slack turns QA_RAG_MAX_ENTRIES into a soft cap, and the
# existing tests are right to forbid that: an operator who caps a corpus at
# 5 should get 5, not 5-plus-whatever. It also bought little in practice,
# since the steady-state-at-cap cost only bites once a corpus is full, while
# the cost this fix actually removes -- parsing the whole file on writes
# that are nowhere near the cap -- is what every real corpus was paying.
def _prune_sync(path: Path, cap: int) -> None:
    try:
        if _disk._count_entries_sync(path) <= cap:
            return
        entries = _disk.load_corpus_sync(path)
        if len(entries) <= cap:
            return
        _atomic_write(path, entries[-cap:])
        logger.info("rag_store: pruned %s to the newest %d entries", path.name, cap)
    except Exception:
        logger.exception("rag_store: prune failed for %s", path)
