"""A screen PNG as the ``{filename, mime, data}`` spec the tool layer attaches."""

from __future__ import annotations

import logging

from tools.mobile.screenshot_resize import _resized

logger = logging.getLogger("tools.mobile.screenshot")

#: The one media type this module produces. ``screencap -p`` writes PNG and
#: nothing else, so this is a fact about the device command rather than a
#: preference -- do not make it configurable without changing that argv.
MIME = "image/png"

#: What a filename may contain. Not a cap and not a bound: the run and case ids
#: are server-generated, and this is the same defensive charset every other
#: tester-facing name in this tree is built from.
_NAME_KEEP = "-_"

#: How much of an id rides in the filename. A NAME, not a payload: the id is
#: already in the packet, and a client shows this string in a file chip.
_NAME_PART_CHARS = 40


def filename_for(run_id: object = "", tc_id: object = "") -> str:
    """A safe, meaningful name for the attached screen. Never raises.

    Meaningful because a chat with twenty attachments in it is unreadable when
    they are all called ``screen.png``, and the run and case ids are what a
    tester would search for. Safe because the name reaches a client's file
    chip: everything outside :data:`_NAME_KEEP` and the alphanumerics goes.
    """
    try:
        parts = []
        for part in (run_id, tc_id):
            cleaned = "".join(
                ch for ch in str(part or "") if ch.isalnum() or ch in _NAME_KEEP
            )[:_NAME_PART_CHARS]
            if cleaned:
                parts.append(cleaned)
        return ("-".join(parts) or "screen") + ".png"
    except Exception:  # never-raise: a filename is not a verdict
        logger.exception("mobile.screenshot.filename_for failed")
        return "screen.png"


#
# ``None`` -- never a spec carrying empty bytes -- because
# ``_image_content_blocks`` would build an empty image block out of one and
# the client would render a broken attachment while the packet's note said a
# picture was there. "There is no picture" and "here is a picture of nothing"
# are different answers and only the first is true.
#
# Bytes are COPIED (``bytes(data)``), so a caller that reuses its buffer
# cannot mutate a spec already handed to the transport.
def to_spec(data: object, *, run_id: object = "", tc_id: object = "") -> dict | None:
    """*data* as the ``{filename, mime, data}`` spec, or ``None``."""
    try:
        if not isinstance(data, (bytes, bytearray)) or not data:
            return None
        # COPIED first (the caller may reuse its buffer), then scaled down to
        # :data:`MAX_LONG_EDGE_PX` -- which is where the image channel's cost is
        # decided, and which cannot touch targeting because no coordinate in
        # this lane comes from a pixel.
        raw = bytes(data)
        return {
            "filename": filename_for(run_id, tc_id),
            "mime": MIME,
            "data": _resized(raw),
        }
    except Exception:  # never-raise: a picture is not a verdict
        logger.exception("mobile.screenshot.to_spec failed")
        return None
