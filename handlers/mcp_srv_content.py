"""MCP content shaping: text blocks, prepare payloads, mobile replies."""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

from mcp.types import TextContent

from handlers import _lane, generate_prepare
from handlers.mcp_srv_progress import _make_progress
from handlers.mcp_srv_track import _tracked


# Owner rule (fix/1630, G05): the server sends NO image to the MCP host, ever.
# The specs stay in the server (report, grounding); the reply is text only.
def _prepare_payload_to_content(result):
    """Convert a PreparePayloadResult into the qa_prepare_test_cases TEXT blocks."""

    text_blocks, _image_specs = generate_prepare.assemble_prepare_payload(result)
    return [TextContent(type="text", text=t) for t in text_blocks]


class MobileReplySpec(NamedTuple):
    """The tool name and ``*_content`` handler of `_mobile_reply` (a parameter object)."""

    name: str
    handler: Callable


def _target_of(fields: dict) -> "_lane.host_way_on.Target":
    """The ``device_id`` and ``package`` a mobile call named (``""`` if none)."""
    return _lane.host_way_on.Target(
        str(fields.get("device_id") or ""), str(fields.get("package") or "")
    )


SCREENSHOT_NOT_SHARED = (
    "\n\n> Screenshots are never shared with you (`screenshot` is accepted but "
    "yields nothing you can view). The server saves this turn's picture in the "
    "run report: cite the Screen id. For layout, colour or imagery use the "
    "element list and the assert kinds, and flag what only a picture can show "
    "with `finding=...` so the tester reviews it in the report. Do not look "
    "for a PNG file."
)


def _screenshot_note(fields: dict) -> str:
    """The note a host that asked for ``screenshot=true`` gets instead of a picture."""
    return SCREENSHOT_NOT_SHARED if fields.get("screenshot") else ""


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
    text = _lane.host_way_on.with_way_on(spec.name, text, _target_of(fields))
    # no reply echoes a tester input the run holds (E08)
    text = _lane.act_scrub.scrub(text) + _screenshot_note(fields)
    return [TextContent(type="text", text=text)]
