"""Single-block markdown/JSON rendering of the host-mode PREPARE payload."""

from __future__ import annotations

import json
import logging

from handlers import generate, gprep_budget, gprep_nextcall
from tools import handover_cost
from tools.tool_briefs import FLOW_BRIEF

logger = logging.getLogger("tools.mcp_handlers")


def _render_head(result: generate.PreparePayloadResult) -> list[str]:
    """The header, next-call block, flow brief and fenced JSON body lines."""
    body = json.dumps(result.payload, ensure_ascii=False, indent=2)
    return [
        "## Host-mode generation -- run this in your own chat, then submit it back",
        "",
        f"**prep_id:** `{result.prep_id}` (pass this to `qa_submit_suite`).",
        *gprep_nextcall._next_call_block(result.payload, str(result.prep_id or "")),
        "",
        FLOW_BRIEF.strip(),
        "",
        "Generate the suite from the payload below, then call `qa_submit_suite` "
        "with this prep_id and your merged JSON:",
        "",
        "```json",
        body,
        "```",
    ]


# What this handover will cost the TESTER's own chat model. Computed from
# the payload being returned in THIS call -- there is no meter and nothing
# accumulates, which is the shape difference from the deleted
# tools/token_meter.py (see tools/handover_cost.py's module docstring).
# Image figures come from `_kept_image_bytes`, i.e. POST-budget: the raw
# attachment list is not what ships.
def _handover_tail(result: generate.PreparePayloadResult) -> list[str]:
    """The handover-cost line, then the notice when there is one."""
    _img_n, _img_b = gprep_budget._kept_image_bytes(result.images)
    parts = [
        "",
        handover_cost.handover_line(
            result.payload, image_count=_img_n, image_bytes=_img_b
        ),
    ]
    if result.notice:
        parts += ["", result.notice]
    return parts


# Render a PreparePayloadResult as a single markdown/JSON text block for a
# string-only caller (a legacy MCP client, or the host-mode branch of
# qa_generate_test_cases in ops-3d-3). ops-3d-2 renders the richer
# multi-block / image form on capable clients. NEVER truncates silently: the
# whole payload is embedded and any disclosure rides in ``notice``.
def render_prepare_payload(result: generate.PreparePayloadResult) -> str:
    """Render a PreparePayloadResult as one text block for a string-only caller."""
    if result.clarify:
        return result.clarify
    if not result.payload:
        return "⚠️ Nothing to prepare."
    parts = _render_head(result)
    parts += _handover_tail(result)
    return "\n".join(parts)
