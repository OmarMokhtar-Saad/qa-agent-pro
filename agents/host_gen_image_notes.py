"""Host mode: reply notes about the ticket's own and the captured screens (C3c).

Moved from handlers/generate.py. The Atlassian MCP server returns attachment
METADATA only, so ticket screenshots cannot ride along as image content; the
notes surface that by NAME instead of silently generating a suite that never
saw them. Tester-typed labels are UNTRUSTED text and ride in wrap_untrusted.
"""

from __future__ import annotations

from agents.host_gen_args import ImageNoteArgs
from agents.host_gen_hooks import ImageHooks
from tools.untrusted import wrap_untrusted

_ATTACHMENTS_UNKNOWN = (
    "> ℹ️ I could not tell whether this ticket has "
    "screenshots — the Jira payload came back without the "
    "`attachment` field. If the ticket has UI images, attach them to "
    "this chat and I'll read them; otherwise the cases below are from "
    "the ticket TEXT only."
)


def _labels_note(url_content: dict) -> str:
    _img_labels = [
        str(x).strip()
        for x in (url_content.get("description_image_labels") or [])[:8]
        if str(x).strip()
    ]
    return (
        " The ticket labels them: " + ", ".join(f"`{x}`" for x in _img_labels) + "."
        if _img_labels
        else ""
    )


def _description_images_note(url_content: dict) -> str:
    # Ahead of attachments_unknown on purpose: when the description
    # embeds images we KNOW the ticket has them. Labels are already
    # charset-gated and capped by jira_mcp._image_ref_labels.
    _n = int(url_content.get("description_image_refs") or 0)
    _label_note = _labels_note(url_content)
    return (
        "> ℹ️ This ticket's description embeds "
        f"{_n} image(s) — UI mockups or screens — that I could "
        "NOT read: Jira is read through your own Atlassian MCP "
        "connection, which returns text, not image bytes. The cases below "
        "come from the ticket TEXT only — attach those screens to "
        f"this chat and I'll read them.{_label_note}"
    )


def ticket_image_note(a: ImageNoteArgs, image: ImageHooks) -> str:
    """What the reply must say about the ticket's own screens, or ""."""
    url_content = a.url_content
    if url_content.get("images_unavailable"):
        # Batch C, C1: partial-fetch aware; "" means nothing to add.
        return image.unreadable_note(
            url_content, attested=a.attested, captured=a.captured
        )
    if url_content.get("description_image_refs") and not url_content.get(
        "images_fetched_server_side"
    ):
        return _description_images_note(url_content)
    if url_content.get("attachments_unknown"):
        # NOT the same as "no attachments": the payload never carried the
        # field, so we cannot tell.
        return _ATTACHMENTS_UNKNOWN
    return ""


def captured_screens_note(cap_labels: list, img_job: bool) -> str:
    """Captured device screens NAMED, or the warning they went nowhere."""
    if not cap_labels:
        return ""
    if img_job:
        return (
            "> 📸 Captured device screens, in the order they are attached to "
            "this reply:\n\n"
            + wrap_untrusted("captured_screen_labels", "\n".join(cap_labels), limit=800)
        )
    return (
        f"> ⚠️ {len(cap_labels)} captured device screen(s) were NOT "
        "forwarded to your model as image content. No image content rides "
        "on this reply, and this server describes no screenshots itself, "
        "so nothing they show is reflected in the cases below."
    )


def missing_captures_note(missing_count: int, rendered: str) -> str:
    """The capture ids that contributed NO screen, or ""."""
    if not missing_count:
        return ""
    return (
        f"> ⚠️ {missing_count} capture id(s) contributed NO screen "
        f"({rendered}). "
        "An id with no "
        "reason beside it "
        "is unknown or expired; one with a reason was pushed out by a "
        "later capture or did not fit this call. Re-run "
        "`qa_capture_screens` if those screens matter."
    )
