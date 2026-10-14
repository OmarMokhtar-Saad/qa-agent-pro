"""Split rendering of an oversized host-mode PREPARE payload into labeled text blocks."""

from __future__ import annotations

import json
import logging

from handlers import gprep_limits, gprep_nextcall
from tools.tool_briefs import FLOW_BRIEF

logger = logging.getLogger("tools.mcp_handlers")

_SPLIT_INTRO = (
    "This grounded payload exceeded the single-block size budget, so it is "
    "split across the labeled blocks below WITHOUT truncation. Reassemble ONE "
    "JSON object with top-level keys `system_prompt`, `user_context`, "
    "`untrusted_data_notice`, `categories` (an array -- one entry per "
    "`categories[i]` block) and `response_schema`; generate the suite; then "
    "call `qa_submit_suite` with this prep_id and your merged JSON."
)


def _split_meta(payload: dict) -> dict:
    """The whitelisted top-level keys that are present, in whitelist order."""
    return {
        k: payload.get(k)
        for k in gprep_limits._SPLIT_META_KEYS
        if payload.get(k) is not None
    }


def _split_header(payload: dict, prep_id: str, n_cats: int) -> list[str]:
    """The header lines up to and including the fenced meta block."""
    meta = _split_meta(payload)
    return [
        f"## Host-mode generation payload -- delivered across {4 + n_cats} labeled parts",
        "",
        f"**prep_id:** `{prep_id}` (pass this to `qa_submit_suite`).",
        *gprep_nextcall._next_call_block(payload, str(prep_id or "")),
        "",
        FLOW_BRIEF.strip(),
        "",
        _SPLIT_INTRO,
        "",
        "### meta",
        "```json",
        json.dumps(meta, ensure_ascii=False, indent=2),
        "```",
    ]


# Split a too-large payload into labeled, self-contained text blocks WITHOUT
# truncating any field. The host reassembles ONE JSON object from the fragments.
def _split_prepare_text_blocks(payload: dict, prep_id: str, notice: str) -> list[str]:
    """Split a too-large payload into labeled, self-contained text blocks."""
    cats = payload.get("categories") or []
    header = _split_header(payload, prep_id, len(cats))
    if notice:
        header += ["", notice]
    return ["\n".join(header), *_split_body_blocks(payload, cats)]


def _fenced(title: str, lang: str, body: str) -> str:
    """One labeled fenced block: title line, opening fence, body, closing fence."""
    return f"{title}\n```{lang}\n{body}\n```"


def _cat_block(i: int, cat: object) -> str:
    """The categories[i] block (name falls back to ? for a non-dict entry)."""
    name = cat.get("name", "?") if isinstance(cat, dict) else "?"
    return _fenced(
        f"### categories[{i}]: {name}",
        "json",
        json.dumps(cat, ensure_ascii=False, indent=2),
    )


def _split_body_blocks(payload: dict, cats: list) -> list[str]:
    """The system_prompt, user_context, response_schema and per-category blocks."""
    schema = json.dumps(
        payload.get("response_schema") or {}, ensure_ascii=False, indent=2
    )
    blocks = [
        _fenced("### system_prompt", "", str(payload.get("system_prompt") or "")),
        _fenced("### user_context", "", str(payload.get("user_context") or "")),
        _fenced("### response_schema", "json", schema),
    ]
    blocks += [_cat_block(i, cat) for i, cat in enumerate(cats)]
    return blocks
