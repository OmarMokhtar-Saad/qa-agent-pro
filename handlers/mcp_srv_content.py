"""MCP content shaping: image blocks, prepare payloads, mobile replies."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import NamedTuple

from fastmcp.utilities import types as _fm_types
from mcp.types import TextContent

from config.settings_guard import never_raise
from handlers import _lane, generate_prepare
from handlers.mcp_srv_progress import _make_progress
from handlers.mcp_srv_state import logger
from handlers.mcp_srv_track import _tracked

_SCREEN_WARN = (
    "could not attach captured screen %r as MCP image content",
    "screen",
)
_TICKET_WARN = (
    "could not attach ticket screenshot %r as MCP image content -- "
    "falling back to its text description",
    "attachment",
)


def _image_block(spec):
    """One MCP image content block built from a {filename, mime, data} spec."""

    mime = spec.get("mime") or "image/png"
    image = _fm_types.Image(data=spec["data"], format=(mime.split("/")[-1] or "png"))
    return image.to_image_content(mime_type=mime)


def _append_image(blocks, spec, warning) -> bool:
    """Append the spec's image block; on failure log ``warning`` and say False."""
    with never_raise(
        logger,
        warning[0],
        spec.get("filename", warning[1]),
        level=logging.WARNING,
        exc_info=True,
    ) as g:
        blocks.append(_image_block(spec))
    return not g.failed


# Same lazy fastmcp import and the same NEVER-silent text fallback as
# _prepare_payload_to_content, which is deliberately left untouched so the
# prepare path stays byte-identical. Used by qa_capture_screens.
def _image_content_blocks(image_specs):
    """Convert {filename, mime, data} specs into MCP image content blocks."""

    blocks: list = []
    for spec in image_specs or []:
        if not _append_image(blocks, spec, _SCREEN_WARN):
            blocks.append(
                TextContent(
                    type="text",
                    text=(
                        "> ℹ️  Captured screen "
                        f"'{spec.get('filename', 'screen')}' could not be "
                        "attached as image content."
                    ),
                )
            )
    return blocks


# Convert a PreparePayloadResult into the content list qa_prepare_test_cases
# returns: one or more TEXT blocks (the grounded payload -- split across blocks
# only when it exceeds the per-block byte budget, NEVER truncated) plus one
# IMAGE block per forwarded ticket screenshot so the host's OWN multimodal model
# sees the real image (item 6). If the fastmcp Image API is unavailable at
# runtime the screenshot degrades to the text description already in the payload
# (image_context) plus a one-line note -- never a silent drop. mcp / fastmcp are
# imported lazily so importing this module never needs the optional extra.
def _prepare_payload_to_content(result):
    """Convert a PreparePayloadResult into the qa_prepare_test_cases content list."""

    text_blocks, image_specs = generate_prepare.assemble_prepare_payload(result)
    blocks: list = [TextContent(type="text", text=t) for t in text_blocks]
    for spec in image_specs:
        if not _append_image(blocks, spec, _TICKET_WARN):
            blocks.append(
                TextContent(
                    type="text",
                    text=(
                        "> ℹ️  Screenshot "
                        f"'{spec.get('filename', 'attachment')}' could not be "
                        "attached as image content; its text description (if any) "
                        "is in the payload above."
                    ),
                )
            )
    return blocks


class MobileReplySpec(NamedTuple):
    """The tool name and ``*_content`` handler of `_mobile_reply` (a parameter object)."""

    name: str
    handler: Callable


def _device_of(fields: dict) -> str:
    """The ``device_id`` a mobile call named, or ``""``."""
    return str(fields.get("device_id") or "")


async def _mobile_reply(spec: MobileReplySpec, ctx, fields: dict, **extra) -> list:
    """Run a mobile ``*_content`` handler under `_tracked` and shape its reply.

    The reply is ONE text block and never an image (fast steps, WP-D): the
    screen reaches the report only through `run_shots.write_shot`, at done and
    at a failed assert.
    """

    # The keyboard idle clock waits while any mobile call is in flight.
    async with _lane.keyboard_idle.busy(str(fields.get("run_id") or "")):
        text, _specs = await _tracked(
            spec.name,
            ctx,
            spec.handler(**fields, **extra, progress=_make_progress(ctx)),
        )
    # a stop never leaves the host without a way on (fix/1490)
    text = _lane.host_way_on.with_way_on(spec.name, text, _device_of(fields))
    return [TextContent(type="text", text=text)]
