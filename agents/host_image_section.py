"""Host mode: the image-description report section (split from host_image.py)."""

from __future__ import annotations

import logging

from agents.host_image_result import _IMG_MAX_ITEMS, _IMG_MAX_NOTES
from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")


_NO_DESCRIPTIONS_NOTE = (
    "> \u2139\ufe0f  The screenshot(s) were forwarded to your chat "
    "instead of being described on this server, but the submission "
    "carried no readable `image_descriptions`. The images may still "
    "have grounded the cases -- this server simply has no record of "
    "what they showed, and it did NOT fall back to a vision call."
)


def _off_topic_head(count: int) -> str:
    """The loud banner above the off-topic lines."""
    return (
        f"> ⚠️  **{count} attached screen(s) may NOT belong "
        "to this ticket.** Your own chat model compared each image "
        "against the ticket text and reported this -- MODEL-DERIVED, "
        "UNTRUSTED and NOT verified by this server, which made no vision "
        "call. If it is right, check whether the cases below leaned on "
        "the wrong screen, and capture or attach the correct one and "
        "prepare again."
    )


def _off_topic_item_line(img: dict) -> str:
    """One bullet for an image whose verdict was `no` or `unsure`."""
    return (
        f">   - `{img.get('image_id', '?')}` — relevant: "
        f"**{img.get('relevant', '?')}** — "
        f"{img.get('relevance_reason', '') or img.get('description', '')}"
    )


# The OFF-TOPIC verdict lines, which go FIRST and loudest.
#
# 2026-08-09: their absence is what let a capture-only run ship a suite
# grounded on a screen from a different feature with no word to the tester.
# Labelled MODEL-DERIVED exactly like the descriptions, because it is: this
# server made no vision call and cannot verify the verdict either way. Nothing
# here blocks the finalize -- see IMAGE_RELEVANCE_JOB for why that is a chosen
# default and not an architectural limit.
def _image_off_topic_lines(result) -> list:
    """The OFF-TOPIC verdict lines, which go FIRST and loudest."""
    off = list(getattr(result, "off_topic", None) or [])
    if not off:
        return []
    lines = [_off_topic_head(len(off))]
    lines += [_off_topic_item_line(img) for img in off[:_IMG_MAX_ITEMS]]
    return lines


def _image_description_lines(result) -> list:
    """Header plus one line per described image."""
    imgs = list(getattr(result, "images", []) or [])
    lines = [
        f"> \U0001f5bc️  **Image descriptions ({len(imgs)}) -- "
        "MODEL-DERIVED by your own chat model.** This server made no "
        "vision call for them; the text below is untrusted input, "
        "URL-stripped and length-capped, and grounds nothing beyond "
        "this report."
    ]
    for img in imgs:
        # The verdict tag is emitted ONLY when a verdict resolved, so a
        # prep that never asked for one renders byte-identically.
        rel = img.get("relevant") or ""
        tag = f" — relevant: **{rel}**" if rel else ""
        lines.append(
            f">   - `{img.get('image_id', '?')}` — {img.get('description', '')}{tag}"
        )
    return lines


def _image_note_lines(result) -> list:
    """One warning bullet per note, capped at _IMG_MAX_NOTES."""
    notes = list(getattr(result, "notes", []) or [])[:_IMG_MAX_NOTES]
    return [f">   - \u26a0\ufe0f  {note}" for note in notes]


# Render the host's image descriptions as a bounded report section.
#
# Says out loud that the descriptions are MODEL-DERIVED and that THIS SERVER
# made no vision call, so a reader never mistakes them for a server-verified
# reading of the screenshot. Returns "" when the job did not run and there is
# nothing honest to report. Never raises.
def build_host_image_section(result) -> str:
    """Render the host's image descriptions as a bounded report section."""
    with never_raise(logger, "build_host_image_section failed", exc_info=True):
        if result is None or not getattr(result, "requested", False):
            return ""
        lines: list = _image_off_topic_lines(result)
        if not getattr(result, "ran", False):
            lines.append(_NO_DESCRIPTIONS_NOTE)
        else:
            lines.extend(_image_description_lines(result))
        lines.extend(_image_note_lines(result))
        return "\n".join(lines) + "\n\n"
    return ""
