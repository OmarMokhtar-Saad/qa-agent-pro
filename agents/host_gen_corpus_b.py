"""Host mode: write a generated suite into the RAG corpus (C3c).

Moved from handlers/generate.py. Best-effort and never disrupts the tool call.
The corpus store is reached through the StoreHooks passed in, so patches on the
handler-side names still bite.
"""

from __future__ import annotations

import logging

from agents.host_gen_args import CorpusJob
from agents.host_gen_hooks import StoreHooks
from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")


def _serialize_case(tc, source_key: str, feature_text_capped: str) -> tuple:
    """(content, metadata) for one case; raises when it does not serialize."""
    steps_text = "\n".join(
        f"{s.step_number}. {s.action} -> {s.expected_result}" for s in tc.steps
    )
    content = f"{tc.title}\n{steps_text}"
    metadata = {
        "feature": tc.module,
        "module": tc.module,
        "tc_id": tc.tc_id,
        "stable_id": tc.stable_id,
    }
    if source_key:
        # The identity replace_source_entries matches on the next
        # generation of this same source.
        metadata["source_key"] = source_key
    if feature_text_capped:
        metadata["feature_text"] = feature_text_capped
    return content, metadata


def corpus_rows(cases: list, source_key: str, feature_text_capped: str) -> list:
    """(content, metadata) for each case that serializes; one that does not is
    logged and skipped."""
    rows: list = []
    for tc in cases:
        with never_raise(
            logger,
            "RAG: could not serialize a test case for corpus — skipping",
            level=logging.WARNING,
        ) as g:
            row = _serialize_case(tc, source_key, feature_text_capped)
        if g.failed:
            continue
        rows.append(row)
    return rows


# Batch C item 2 (2026-08-09): REPLACE, do not append. Re-generating the same
# ticket used to add a whole second copy of its cases: the corpus grew
# 571 -> 676 docs in ONE day of re-runs, and a later prepare saw 5/5 RAG hits
# flagged as duplicate risk -- i.e. the retrieval half had started grounding
# new generations on its own echoes, and the only bound on the store was
# QA_RAG_MAX_ENTRIES pruning the OLDEST entries, which is a cap, not
# de-duplication.
#
# ORDER MATTERS (review C2): the fresh rows are written FIRST and the
# superseded ones are pruned AFTER, keyed on this source and always sparing
# the ids just written. A delete-then-write would have destroyed the previous
# good suite whenever the new one turned out to be empty or entirely
# unserializable -- i.e. exactly when the tester still needs the old one. In
# the worst case this order leaves BOTH copies (a prune failure is logged,
# never raised), which is the pre-2026-08-09 behaviour and so never a
# regression.
#
# PARTIAL WRITES: NEWEST WINS (review N4). If some cases serialized and
# others did not, the prune still runs, so this source keeps the cases that
# were written NOW and loses the older copies of the ones that failed. The
# alternative -- prune only on a complete write -- was rejected: a single
# unserializable case would then leave the duplicate pair in place forever,
# which is the very defect this change exists to end, and the corpus is a
# grounding aid, not a system of record (the suite itself lives in
# suite_store and the .xlsx export).
# feature_text matters here: without it a pasted-description suite has no
# identity and silently falls back to append.
async def persist_suite_to_corpus(job: CorpusJob, store: StoreHooks) -> None:
    """Write each generated test case into the RAG corpus (QW-6 / I-014 / F7).

    This is the *write* half of the RAG loop; query_corpus in
    test_scenario_agent is the read half. Best-effort and never disrupts the
    tool call: a serialization or disk error for one case is logged and
    skipped (add_to_corpus is itself never-raise).
    """
    cases = getattr(job.suite, "test_cases", None) or []
    feature_text_capped = (job.feature_text or "").strip()[: store.feature_text_cap()]
    source_key = store.corpus_source_key(job.source_url, job.feature_text)
    rows = corpus_rows(cases, source_key, feature_text_capped)
    fresh_ids = await write_corpus_rows(rows, store)
    await prune_superseded_cases(source_key, fresh_ids, store)


async def write_corpus_rows(rows, store: StoreHooks) -> list:
    """Write each row to the corpus; return the ids of the rows written."""
    written = 0
    fresh_ids: list = []
    for content, metadata in rows:
        with never_raise(
            logger,
            "RAG: add_to_corpus failed for a test case — ignoring",
            level=logging.WARNING,
        ):
            result = await store.add_to_corpus("test_case", content, metadata)
            if not result.get("error"):
                written += 1
                # Keep the id so the prune below spares the row it just wrote.
                _new_id = str((result.get("content") or {}).get("id") or "")
                if _new_id:
                    fresh_ids.append(_new_id)
    if written:
        logger.info("RAG: persisted %d test case(s) to corpus", written)
    return fresh_ids


async def prune_superseded_cases(
    source_key: str, fresh_ids: list, store: StoreHooks
) -> None:
    """Drop the older copies of a source's cases, sparing the fresh ids."""
    if source_key and fresh_ids:
        _replaced = await store.replace_source_entries(
            "test_case", source_key, keep_ids=fresh_ids
        )
        _removed = int(((_replaced or {}).get("content") or {}).get("removed") or 0)
        if _removed:
            logger.info("RAG: removed %d superseded case(s) for this source", _removed)
    else:
        _log_unpruned(source_key)


def _log_unpruned(source_key: str) -> None:
    """Say why nothing was pruned for this source."""
    if source_key:
        logger.debug(
            "RAG: nothing was persisted for this source -- the previously stored "
            "cases were left exactly as they were"
        )
    else:
        logger.debug(
            "RAG: this suite has no source key (no http(s) source URL and no "
            "feature text) -- appending, since the corpus cannot de-duplicate a "
            "source it cannot name"
        )
