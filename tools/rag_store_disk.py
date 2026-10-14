"""JSONL corpus disk I/O for the RAG store: parse cache, load, append, count.

Pure with respect to settings: callers pass paths in. Blocking I/O only; the
facade wraps these in asyncio.to_thread().
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path

logger = logging.getLogger("tools.rag_store")


# In-process parse cache for load_corpus_sync, keyed by str(path) ->
# ((mtime_ns, size), entries). Loads run inside asyncio.to_thread worker
# threads, so access is guarded by a threading.Lock. Any write to a corpus
# file (append via _save_entry_sync, atomic replace via _prune_sync) changes
# its mtime/size, so the key differs and the stale entry is bypassed -- no
# explicit invalidation is needed.
_CORPUS_CACHE: dict[str, tuple[tuple[int, int], list[dict]]] = {}
_CORPUS_CACHE_LOCK = threading.Lock()


def _stat_key(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _cache_get(str_path: str, cache_key: tuple[int, int]) -> list[dict] | None:
    with _CORPUS_CACHE_LOCK:
        cached = _CORPUS_CACHE.get(str_path)
        if cached is not None and cached[0] == cache_key:
            return cached[1]
    return None


def _cache_put(str_path: str, cache_key: tuple[int, int], entries: list) -> None:
    with _CORPUS_CACHE_LOCK:
        _CORPUS_CACHE[str_path] = (cache_key, entries)


def _append_line(line: str, entries: list[dict], path: Path) -> None:
    line = line.strip()
    if not line:
        return
    try:
        entries.append(json.loads(line))
    except json.JSONDecodeError:
        logger.warning("rag_store: skipping corrupt JSONL line in %s", path)


def _read_entries(path: Path, entries: list[dict]) -> bool:
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                _append_line(line, entries, path)
    except OSError as exc:
        logger.warning("rag_store: could not read corpus file %s: %s", path, exc)
        return False
    return True


# Read a JSONL file and return a list of entry dicts. Returns [] on any error.
#
# Parsed entries are cached in-process keyed by (path, mtime_ns, size): a hot
# chat turn that re-queries the corpus no longer re-reads and re-parses the
# whole file from disk. Any write -- an append via _save_entry_sync or the
# atomic replace in _prune_sync -- changes the file's mtime and/or size, so the
# key naturally differs and the stale entry is bypassed. No explicit
# invalidation is needed, which keeps cross-session writers correct.
def load_corpus_sync(path: Path) -> list[dict]:
    if not path.exists():
        return []
    cache_key = _stat_key(path)
    str_path = str(path)
    if cache_key is not None:
        hit = _cache_get(str_path, cache_key)
        if hit is not None:
            return hit
    entries: list[dict] = []
    if not _read_entries(path, entries):
        return entries
    if cache_key is not None:
        _cache_put(str_path, cache_key, entries)
    return entries


def _save_entry_sync(path: Path, entry: dict) -> None:
    """Append a single entry as a JSONL line. Creates parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


# Entry count by counting NEWLINES, without parsing any JSON.
#
# The gate below only needs to know "roughly how many", and parsing was the
# expensive half: measured on a real 5033-entry / 2.0 MB corpus, one add cost
# 112 ms against 1.4 ms on a 178-entry one -- almost all of it
# ``load_corpus_sync`` deserialising every entry just to compare a length, on
# every write, including the overwhelming majority that were nowhere near the
# cap. A byte scan is orders of magnitude cheaper and cannot raise on a
# malformed line. Returns 0 on any error, which makes the caller skip pruning --
# the safe direction, since an unpruned corpus only costs disk.
def _count_entries_sync(path: Path) -> int:
    try:
        if not path.is_file():
            return 0
        total = 0
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                total += chunk.count(b"\n")
        return total
    except OSError:
        logger.debug("rag_store: could not count %s", path, exc_info=True)
        return 0


def _file_signature(path: Path) -> tuple[int, int] | None:
    """The file's (size, mtime_ns), or None when it cannot be stat'ed."""
    try:
        st = path.stat()
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def _new_entry(entry_type: str, content: str, metadata: dict) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "type": entry_type,
        "content": content,
        "metadata": metadata or {},
        "added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


_SAFE_ENTRY_TYPE = re.compile(r"^[A-Za-z0-9_]+$")


# Map an entry_type to its JSONL corpus file under *base*, fail-closed.
#
# entry_type becomes a filename component, so anything that is not a bare
# identifier (path separators, ``..``, other punctuation) is rejected as a
# path-traversal attempt and coerced to the default ``test_cases`` corpus
# rather than escaping ``qa_rag_storage_path``.
def _corpus_file(base: Path, entry_type: str) -> Path:
    if entry_type == "test_case":
        return base / "test_cases.jsonl"
    if entry_type == "bug_report":
        return base / "bug_reports.jsonl"
    if not isinstance(entry_type, str) or not _SAFE_ENTRY_TYPE.match(entry_type):
        logger.warning(
            "rag_store: rejecting unsafe entry_type %r — using test_cases corpus",
            entry_type,
        )
        return base / "test_cases.jsonl"
    return base / f"{entry_type}.jsonl"


def _load_corpus_sync(path: Path) -> list[dict]:
    """Old private name (patched by foreign tests / outside callers); calls through."""
    return load_corpus_sync(path)
