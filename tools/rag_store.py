"""RAG Store — lightweight TF-IDF/Jaccard corpus for test cases and bug reports.

Contract (never-raises):
  On success: {"error": None, "content": <value>}
  On failure: {"error": str, "content": None}

Storage: JSONL files under settings.qa_rag_storage_path.
All blocking disk I/O is wrapped in asyncio.to_thread().
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path

from config.settings import settings
from tools.embeddings import backend_enabled, cosine_similarity, embed_texts
from tools.install_paths import resolve_data_path

logger = logging.getLogger(__name__)


# In-process parse cache for _load_corpus_sync, keyed by str(path) ->
# ((mtime_ns, size), entries). Loads run inside asyncio.to_thread worker
# threads, so access is guarded by a threading.Lock. Any write to a corpus
# file (append via _save_entry_sync, atomic replace via _prune_sync) changes
# its mtime/size, so the key differs and the stale entry is bypassed -- no
# explicit invalidation is needed.
_CORPUS_CACHE: dict[str, tuple[tuple[int, int], list[dict]]] = {}
_CORPUS_CACHE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> set[str]:
    """Split text on whitespace and punctuation, lowercase all tokens."""
    return set(re.split(r"[\W_]+", text.lower())) - {""}


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Return |A∩B| / |A∪B|, or 0.0 when both sets are empty."""
    if not set_a and not set_b:
        return 0.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def _tokenize_list(text: str) -> list[str]:
    """Like _tokenize but keeps token counts (for TF-IDF)."""
    return [t for t in re.split(r"[\W_]+", text.lower()) if t]


def _cosine_tfidf_scores(
    query_tokens: list[str], docs_tokens: list[list[str]]
) -> list[float]:
    """Cosine similarity between a query and each doc under a TF-IDF weighting
    computed over the docs + query (opt-in dedup mode, I-051). Pure Python, no
    external model. Returns one score in [0, 1] per doc. Never raises."""
    import math
    from collections import Counter

    try:
        corpus = docs_tokens + [query_tokens]
        n = len(corpus)
        df: Counter = Counter()
        for toks in corpus:
            for t in set(toks):
                df[t] += 1

        def _idf(t: str) -> float:
            return math.log((n + 1) / (df.get(t, 0) + 1)) + 1.0

        def _vec(toks: list[str]) -> dict:
            if not toks:
                return {}
            tf = Counter(toks)
            total = len(toks)
            return {t: (c / total) * _idf(t) for t, c in tf.items()}

        qv = _vec(query_tokens)
        qnorm = math.sqrt(sum(v * v for v in qv.values())) or 1.0
        scores: list[float] = []
        for toks in docs_tokens:
            dv = _vec(toks)
            dot = sum(qv.get(t, 0.0) * w for t, w in dv.items())
            dnorm = math.sqrt(sum(v * v for v in dv.values())) or 1.0
            scores.append(dot / (qnorm * dnorm))
        return scores
    except Exception:
        logger.exception("_cosine_tfidf_scores failed — returning zeros")
        return [0.0] * len(docs_tokens)


def _bm25_scores(
    query_tokens: list[str],
    docs_tokens: list[list[str]],
    k1: float = 1.2,
    b: float = 0.75,
) -> list[float]:
    """Okapi BM25 — the consensus lexical retrieval baseline: term-frequency
    saturation + document-length normalization outrank flat overlap and
    TF-IDF on technical text (steps, identifiers, feature names).

    Raw BM25 is unbounded, so scores are saturation-normalized to [0, 1)
    via s/(s+10) — the existing qa_rag_similarity_threshold semantics keep
    working across modes (a raw BM25 of ~4.3 ≈ 0.3 normalized). Pure Python,
    default parameters per the literature; never raises."""
    import math
    from collections import Counter

    try:
        n = len(docs_tokens)
        if n == 0:
            return []
        avgdl = (sum(len(d) for d in docs_tokens) / n) or 1.0
        df: Counter = Counter()
        for toks in docs_tokens:
            for t in set(toks):
                df[t] += 1

        def _idf(t: str) -> float:
            d = df.get(t, 0)
            return math.log(1.0 + (n - d + 0.5) / (d + 0.5))

        scores: list[float] = []
        for toks in docs_tokens:
            tf = Counter(toks)
            dl = len(toks) or 1
            s = 0.0
            for t in query_tokens:
                f = tf.get(t, 0)
                if not f:
                    continue
                s += _idf(t) * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
            scores.append(s / (s + 10.0) if s > 0 else 0.0)
        return scores
    except Exception:
        logger.exception("_bm25_scores failed — returning zeros")
        return [0.0] * len(docs_tokens)


async def _apply_semantic_scores(
    query_text: str, scored: list[tuple[float, dict]]
) -> list[tuple[float, dict]]:
    """Overlay cosine(query, entry) onto the lexical scores for entries that carry
    a stored 'embedding' vector. Vectorless (legacy) entries keep their lexical
    score, so mixed corpora stay backward compatible. Returns the input unchanged
    on any embedding failure or when no entry has a vector. Never raises."""
    try:
        if not any(isinstance(e.get("embedding"), list) for _s, e in scored):
            return scored
        q = await embed_texts([query_text])
        if q.get("error") or not q.get("content"):
            return scored
        qv = q["content"][0]
        out: list[tuple[float, dict]] = []
        for s, e in scored:
            vec = e.get("embedding")
            if isinstance(vec, list) and len(vec) == len(qv):
                out.append((max(0.0, cosine_similarity(qv, vec)), e))
            else:
                out.append((s, e))
        return out
    except Exception:
        logger.exception("_apply_semantic_scores failed — keeping lexical scores")
        return scored


def _load_corpus_sync(path: Path) -> list[dict]:
    """Read a JSONL file and return a list of entry dicts. Returns [] on any error.

    Parsed entries are cached in-process keyed by (path, mtime_ns, size): a hot
    chat turn that re-queries the corpus no longer re-reads and re-parses the
    whole file from disk. Any write -- an append via _save_entry_sync or the
    atomic replace in _prune_sync -- changes the file's mtime and/or size, so the
    key naturally differs and the stale entry is bypassed. No explicit
    invalidation is needed, which keeps cross-session writers correct."""
    if not path.exists():
        return []
    try:
        st = path.stat()
        cache_key = (st.st_mtime_ns, st.st_size)
    except OSError:
        cache_key = None
    str_path = str(path)
    if cache_key is not None:
        with _CORPUS_CACHE_LOCK:
            cached = _CORPUS_CACHE.get(str_path)
            if cached is not None and cached[0] == cache_key:
                return cached[1]
    entries: list[dict] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("rag_store: skipping corrupt JSONL line in %s", path)
    except OSError as exc:
        logger.warning("rag_store: could not read corpus file %s: %s", path, exc)
        return entries
    if cache_key is not None:
        with _CORPUS_CACHE_LOCK:
            _CORPUS_CACHE[str_path] = (cache_key, entries)
    return entries


def _save_entry_sync(path: Path, entry: dict) -> None:
    """Append a single entry as a JSONL line. Creates parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _count_entries_sync(path: Path) -> int:
    """Entry count by counting NEWLINES, without parsing any JSON.

    The gate below only needs to know "roughly how many", and parsing was the
    expensive half: measured on a real 5033-entry / 2.0 MB corpus, one add cost
    112 ms against 1.4 ms on a 178-entry one -- almost all of it
    ``_load_corpus_sync`` deserialising every entry just to compare a length, on
    every write, including the overwhelming majority that were nowhere near the
    cap. A byte scan is orders of magnitude cheaper and cannot raise on a
    malformed line. Returns 0 on any error, which makes the caller skip pruning --
    the safe direction, since an unpruned corpus only costs disk.
    """
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


def _prune_sync(path: Path, cap: int) -> None:
    """Keep only the newest ``cap`` entries of a JSONL corpus file (append
    order is chronological). Atomic rewrite; failures are logged, never
    raised — a failed prune just leaves the corpus slightly over cap."""
    try:
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
        if _count_entries_sync(path) <= cap:
            return
        entries = _load_corpus_sync(path)
        if len(entries) <= cap:
            return
        # Unique per-call temp name (+ pid) so two cross-session writers pruning
        # the same corpus can't clobber a shared ".jsonl.tmp" and lose entries.
        tmp = path.with_suffix(f".{os.getpid()}.{uuid.uuid4().hex}.jsonl.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                for e in entries[-cap:]:
                    fh.write(json.dumps(e, ensure_ascii=False) + "\n")
            tmp.replace(path)
        finally:
            # A crashed prune must not leave its unique temp orphan behind.
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        logger.info("rag_store: pruned %s to the newest %d entries", path.name, cap)
    except Exception:
        logger.exception("rag_store: prune failed for %s", path)


def _delete_source_sync(path: Path, source_key: str, keep_ids: set) -> int:
    """Drop entries whose ``metadata.source_key`` is *source_key* and whose
    ``id`` is NOT in *keep_ids*.

    Returns how many were removed -- 0 when the file is missing, unreadable, or
    holds nothing to remove. Atomic rewrite through a unique pid+uuid temp name,
    exactly the discipline _prune_sync uses, so two cross-session writers cannot
    clobber a shared temp; a failure is logged and NEVER raised, which leaves the
    corpus untouched and the caller keeps both copies -- the behaviour that
    predates this function.

    LOST-WRITE GUARD (review W1): this is a read-modify-write over a file another
    process may be APPENDING to (_save_entry_sync), and a plain rewrite would
    silently drop anything appended between the read and the replace. The file's
    (size, mtime_ns) is captured with the read and re-checked immediately before
    tmp.replace; if it moved, the replace is ABANDONED and 0 is returned. That
    trades a missed de-duplication (the next run removes it) against never losing
    another writer's entry. It does not make the pre-existing _prune_sync race
    disappear -- it only refuses to widen it.

    It can only ever remove entries THIS code path stamped: ``source_key`` did
    not exist before 2026-08-09, so no legacy entry can match. Entries that are
    not dicts, or carry no metadata dict, are always kept."""
    try:
        if not path.is_file():
            return 0
        # STAT FIRST, then read (review N1): taken after the load it would
        # observe the state INCLUDING a concurrent append, so the re-check below
        # would compare post-append against post-append and always pass -- an
        # inert guard that still dropped the line.
        try:
            st = path.stat()
            before = (st.st_size, st.st_mtime_ns)
        except OSError:
            before = None
        entries = _load_corpus_sync(path)

        def _drop(entry) -> bool:
            if not isinstance(entry, dict):
                return False
            md = entry.get("metadata")
            if not isinstance(md, dict):
                return False
            if str(md.get("source_key") or "") != source_key:
                return False
            return str(entry.get("id") or "") not in keep_ids

        keep = [e for e in entries if not _drop(e)]
        removed = len(entries) - len(keep)
        if removed <= 0:
            return 0
        tmp = path.with_suffix(f".{os.getpid()}.{uuid.uuid4().hex}.jsonl.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                for e in keep:
                    fh.write(json.dumps(e, ensure_ascii=False) + "\n")
            try:
                st2 = path.stat()
                after = (st2.st_size, st2.st_mtime_ns)
            except OSError:
                after = None
            if before is not None and after is not None and after != before:
                logger.warning(
                    "rag_store: %s changed under a source replace -- keeping both "
                    "copies rather than losing a concurrent write",
                    path.name,
                )
                return 0
            tmp.replace(path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return removed
    except Exception:
        logger.exception("rag_store: source replace failed for %s", path)
        return 0


_SAFE_ENTRY_TYPE = re.compile(r"^[A-Za-z0-9_]+$")


def _corpus_path(entry_type: str) -> Path:
    """Map an entry_type to its JSONL corpus file, fail-closed.

    entry_type becomes a filename component, so anything that is not a bare
    identifier (path separators, ``..``, other punctuation) is rejected as a
    path-traversal attempt and coerced to the default ``test_cases`` corpus
    rather than escaping ``qa_rag_storage_path``."""
    # Install-root anchored -- see tools/install_paths. A cwd-relative corpus split
    # the history, so grounding saw a fraction of it, and one copy reached the
    # entry cap where every write rewrites the whole file.
    base = resolve_data_path(settings.qa_rag_storage_path)
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def add_to_corpus(entry_type: str, content: str, metadata: dict) -> dict:
    """Append a new entry to the corpus.

    Returns {"error": None, "content": {"id": str}} on success.
    Returns {"error": str, "content": None} on any exception.
    Never raises.
    """
    try:
        entry_id = str(uuid.uuid4())
        entry = {
            "id": entry_id,
            "type": entry_type,
            "content": content,
            "metadata": metadata or {},
            "added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        # Optional semantic vector (QA_EMBEDDINGS_BACKEND). Never blocks the add:
        # embed_texts is never-raise and a failure just omits the vector. Rounded
        # to 6 decimals to cap corpus (JSONL) bloat.
        if backend_enabled():
            emb = await embed_texts([content])
            if not emb.get("error") and emb.get("content"):
                entry["embedding"] = [round(float(x), 6) for x in emb["content"][0]]
        path = _corpus_path(entry_type)
        await asyncio.to_thread(_save_entry_sync, path, entry)
        # Strict type check: pydantic guarantees a real int in production;
        # anything else (e.g. a fully-mocked settings) means "no cap".
        cap_raw = getattr(settings, "qa_rag_max_entries", 0)
        cap = cap_raw if isinstance(cap_raw, int) else 0
        if cap > 0:
            await asyncio.to_thread(_prune_sync, path, cap)
        # DEMOTED from INFO (2026-07-30 run review, item 8): one line PER corpus
        # entry meant 65 lines on a single submit, and because all logging goes to
        # stderr Cursor renders every one of them as `[error]`, burying real
        # errors. The aggregate "RAG: persisted N test case(s) to corpus" line in
        # tools/mcp_handlers._persist_suite_to_corpus carries the operational fact.
        logger.debug("rag_store: added %s entry %s to %s", entry_type, entry_id, path)
        return {"error": None, "content": {"id": entry_id}}
    except Exception as exc:
        logger.exception("rag_store.add_to_corpus failed")
        return {"error": str(exc), "content": None}


async def replace_source_entries(
    entry_type: str, source_key: str, *, keep_ids: object = None
) -> dict:
    """Remove a source's SUPERSEDED entries so a re-run replaces, not appends.

    The write half of the RAG loop was append-only, so re-generating the same
    ticket stored a second full copy of its cases. The corpus grew 571 -> 676
    docs in one day of re-runs and a later prepare saw 5/5 retrieved neighbours
    flagged as duplicate risk. QA_RAG_MAX_ENTRIES bounds the FILE, not the
    duplication -- it prunes the OLDEST entries, which is precisely the wrong
    end.

    ``keep_ids`` is the set of entry ids the caller has JUST written for this
    source; they are spared and everything else under the same key goes. Called
    with no keep_ids it removes every entry for the source, which is why
    tools/mcp_handlers._persist_suite_to_corpus calls it only AFTER a successful
    write and only with the fresh ids: an empty or wholly-unserializable suite
    must never delete the previous good one.

    An empty key is a no-op (0 removed) -- that is how the caller says "this
    suite has no source identity, just append".

    Returns {"error": None, "content": {"removed": int}} on success and
    {"error": str, "content": None} on failure. Never raises.
    """
    try:
        key = str(source_key or "").strip()
        if not key:
            return {"error": None, "content": {"removed": 0}}
        keep = {str(i) for i in (keep_ids or []) if str(i or "")}
        path = _corpus_path(entry_type)
        removed = await asyncio.to_thread(_delete_source_sync, path, key, keep)
        if removed:
            logger.info(
                "rag_store: removed %d superseded entry(ies) for one source in %s",
                removed,
                path.name,
            )
        return {"error": None, "content": {"removed": removed}}
    except Exception as exc:
        logger.exception("rag_store.replace_source_entries failed")
        return {"error": str(exc), "content": None}


async def query_corpus(
    query_text: str,
    entry_type: str | None = None,
    top_k: int = 5,
    metadata_filter: dict | None = None,
) -> dict:
    """Find the top-k corpus entries most similar to query_text.

    Similarity is Jaccard overlap on whitespace+punctuation tokens.

    Returns {"error": None, "content": list[{"content": str, "metadata": dict, "score": float}]}
    on success (list may be empty if corpus is empty or query produces no tokens).
    Returns {"error": str, "content": None} on any exception.
    Never raises.
    """
    try:
        if not query_text or not query_text.strip():
            return {"error": None, "content": []}

        query_tokens = _tokenize(query_text)
        if not query_tokens:
            return {"error": None, "content": []}

        if entry_type is not None:
            paths = [_corpus_path(entry_type)]
        else:
            paths = [_corpus_path("test_case"), _corpus_path("bug_report")]

        all_entries: list[dict] = []
        for path in paths:
            entries = await asyncio.to_thread(_load_corpus_sync, path)
            all_entries.extend(entries)

        if not all_entries:
            return {"error": None, "content": []}

        usable = [e for e in all_entries if (e.get("content") or "")]

        if metadata_filter:
            # Case-insensitive substring match on each requested metadata key —
            # narrowing before scoring is the highest-value dedup lever.
            def _md_match(entry: dict) -> bool:
                md = entry.get("metadata") or {}
                return all(
                    str(v).lower() in str(md.get(k, "")).lower()
                    for k, v in metadata_filter.items()
                    if v
                )

            usable = [e for e in usable if _md_match(e)]

        mode = (
            getattr(settings, "qa_rag_similarity_mode", "jaccard") or "jaccard"
        ).lower()
        if mode not in ("jaccard", "cosine", "bm25"):
            logger.warning(
                "Unknown QA_RAG_SIMILARITY_MODE=%r — falling back to 'jaccard'",
                mode,
            )
            mode = "jaccard"
        if mode == "bm25":
            docs_tokens = [_tokenize_list(e["content"]) for e in usable]
            bm25 = _bm25_scores(_tokenize_list(query_text), docs_tokens)
            scored = list(zip(bm25, usable))
        elif mode == "cosine":
            # TF-IDF cosine gives rare/discriminative terms more weight than
            # Jaccard's flat set overlap — better "we already have this" dedup.
            docs_tokens = [_tokenize_list(e["content"]) for e in usable]
            cos_scores = _cosine_tfidf_scores(_tokenize_list(query_text), docs_tokens)
            scored = list(zip(cos_scores, usable))
        else:
            scored = [
                (_jaccard_similarity(query_tokens, _tokenize(e["content"])), e)
                for e in usable
            ]

        # Semantic overlay (QA_EMBEDDINGS_BACKEND, opt-in): replace the lexical
        # score with query<->entry cosine for every entry that carries a stored
        # vector. Vectorless (legacy) entries keep their lexical score, so mixed
        # corpora stay backward compatible. Any failure leaves `scored` untouched.
        if backend_enabled():
            scored = await _apply_semantic_scores(query_text, scored)

        hl_raw = getattr(settings, "qa_rag_recency_half_life_days", 0)
        half_life = hl_raw if isinstance(hl_raw, int) else 0
        if half_life > 0:
            # Freshness boost (research-backed): newer entries get up to +15%,
            # decaying exponentially with the configured half-life. Capped at
            # 1.0 so score bounds hold in every mode.
            import calendar
            import math

            now = time.time()

            def _age_days(entry: dict) -> float:
                raw = entry.get("added_at") or ""
                try:
                    ts = calendar.timegm(time.strptime(raw, "%Y-%m-%dT%H:%M:%SZ"))
                except (ValueError, OverflowError):
                    return float("inf")
                return max(0.0, (now - ts) / 86400.0)

            scored = [
                (
                    min(1.0, s * (1.0 + 0.15 * math.exp(-_age_days(e) / half_life))),
                    e,
                )
                for s, e in scored
            ]

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:top_k]

        results = [
            {
                "content": entry.get("content", ""),
                "metadata": entry.get("metadata", {}),
                "score": round(score, 4),
            }
            for score, entry in top
            if score > 0
        ]

        logger.info(
            "rag_store.query_corpus: query returned %d/%d results (top score %.4f)",
            len(results),
            len(all_entries),
            results[0]["score"] if results else 0.0,
        )
        return {"error": None, "content": results}
    except Exception as exc:
        logger.exception("rag_store.query_corpus failed")
        return {"error": str(exc), "content": None}
