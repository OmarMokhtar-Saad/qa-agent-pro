"""Turn a host-mode PREPARE result into the MCP text blocks and image specs."""

from __future__ import annotations

import logging

from handlers import (
    generate,
    gprep_budget,
    gprep_limits,
    gprep_render,
    gprep_select,
    gprep_split,
)
from tools import handover_cost

logger = logging.getLogger("tools.mcp_handlers")


def _split_with_cost(result: generate.PreparePayloadResult, payload: dict) -> list[str]:
    """The split text blocks plus the handover-cost block."""
    text_blocks = gprep_split._split_prepare_text_blocks(
        payload, result.prep_id, result.notice
    )
    # The split path does NOT go through render_prepare_payload, so the
    # handover-cost block has to be attached here as well -- otherwise the
    # LARGEST payloads, the ones actually worth disclosing, are the only
    # ones that disclose nothing. Single-block payloads get it from the
    # renderer, so it is never added twice.
    _split_img_n, _split_img_b = gprep_budget._kept_image_bytes(result.images)
    return [
        *text_blocks,
        handover_cost.handover_line(
            payload,
            image_count=_split_img_n,
            image_bytes=_split_img_b,
        ),
    ]


# Turn a PreparePayloadResult into (text_blocks, image_specs) for the MCP
# tool layer. text_blocks is ONE legacy-identical block when the payload fits
# _MAX_TEXT_BLOCK_BYTES, else the split set (item 5); image_specs is the
# byte-budget-capped {filename, mime, data} list (item 6). Any image dropped for
# size is disclosed in an extra text block. Pure -- imports no fastmcp/mcp, so
# it is fully unit-testable; the tool layer converts the specs to ImageContent.
def assemble_prepare_payload(
    result: generate.PreparePayloadResult,
) -> tuple[list[str], list[dict]]:
    """Turn a PreparePayloadResult into (text_blocks, image_specs) for the MCP tool layer."""
    if result.clarify or not result.payload:
        return ([gprep_render.render_prepare_payload(result)], [])
    image_specs, disclosure = gprep_select._select_prepare_images(result)
    single = gprep_render.render_prepare_payload(result)
    if len(single.encode("utf-8")) <= gprep_limits._MAX_TEXT_BLOCK_BYTES:
        text_blocks = [single]
    else:
        text_blocks = _split_with_cost(result, result.payload)
    if disclosure:
        text_blocks = [*text_blocks, disclosure]
    return (text_blocks, image_specs)
