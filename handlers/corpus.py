"""Moved from handlers (refactor P2): corpus."""

from __future__ import annotations

import logging
from typing import NamedTuple

from handlers import _common, _reply
from handlers._boundary import handler_guard
from handlers.corpus_mirror import handle_mirror_hold  # noqa: F401
from tools import settings_port
from tools.error_boundary import text_reply
from tools.rag_store import RagQuery, query_corpus

logger = logging.getLogger("tools.mcp_handlers")


def shape_corpus_hits(query: str, hits: list) -> str:
    if not hits:
        return f"No corpus matches for **{_reply._clip_echo(query)}**."
    lines = [
        f"## Corpus matches for “{_reply._clip_echo(query)}” ({len(hits)})",
        "",
    ]
    for hit in hits[:10]:
        score = hit.get("score", 0.0)
        snippet = (hit.get("content") or "").replace("\n", " ").strip()[:160]
        meta = hit.get("metadata") or {}
        label = meta.get("feature") or meta.get("description") or ""
        prefix = f"{label}: " if label else ""
        lines.append(f"- (score={score:.2f}) {prefix}{snippet}")
    return "\n".join(lines)


class CorpusQuery(NamedTuple):
    """The inputs of `handle_search_corpus` (a parameter object)."""

    query: str
    entry_type: str = "test_case"
    feature: str = ""


async def handle_search_corpus(
    req: CorpusQuery,
    *,
    progress: _common.ProgressCb = None,
) -> str:
    query, entry_type, feature = req
    query = (query or "").strip()
    if not query:
        return "⚠️ Provide a search query."
    if not _common.rag_enabled():
        return "ℹ️ Corpus search is disabled in this build."
    entry_type = (entry_type or "test_case").strip()
    if entry_type not in ("test_case", "bug_report"):
        return f"⚠️ Unknown corpus '{entry_type}'. Choose 'test_case' or 'bug_report'."
    return await _search(query, entry_type, feature)


@handler_guard(
    "qa_search_corpus",
    "handle_search_corpus failed",
    text_reply("⚠️ Corpus search failed: "),
)
async def _search(query: str, entry_type: str, feature: str) -> str:
    result = await query_corpus(
        query,
        RagQuery(
            entry_type=(entry_type or "test_case"),
            top_k=settings_port.get().qa_rag_top_k,
            metadata_filter=(
                {"feature": feature.strip()} if (feature or "").strip() else None
            ),
        ),
    )
    if result.get("error"):
        return f"⚠️ Corpus search failed: {result['error']}"
    hits = result.get("content") or []
    await _common.audit(
        "mcp_search_corpus", detail={"query": query[:120], "entry_type": entry_type}
    )
    return shape_corpus_hits(query, hits)
