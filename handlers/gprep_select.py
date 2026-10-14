"""Pick the ticket screenshots that ride on the host-mode PREPARE reply."""

from __future__ import annotations

import sys

from handlers import generate, gprep_budget
from handlers.seam_alias import alias


def _kept_specs(images: list, flags: list[bool]) -> list[dict]:
    """The {filename, mime, data} specs of the images whose flag is True."""
    return [
        {
            "filename": img.get("filename") or "attachment",
            "mime": (img.get("mime") or "image/png"),
            "data": bytes(img.get("data")),
        }
        for img, ok in zip(images, flags)
        if ok
    ]


def dropped_note(dropped: list[str]) -> str:
    """The disclosure that NAMES every image dropped for size ("" if none)."""
    if not dropped:
        return ""
    return (
        "> ℹ️  "
        f"{len(dropped)} ticket screenshot(s) were NOT attached as image "
        "content to keep the reply within the size budget "
        f"({', '.join(dropped)}). Their text description, if any, is in the "
        "payload above."
    )


# Pick the ticket screenshots that fit the per-result byte budget and the
# jira_max_images count cap. Returns (kept, disclosure): kept is a list of
# {filename, mime, data} dicts; disclosure NAMES every image dropped for size
# (never silent). Pure -- no fastmcp import.
def _select_prepare_images(
    result: generate.PreparePayloadResult,
) -> tuple[list[dict], str]:
    """Pick the ticket screenshots that fit the budget: (kept, disclosure)."""
    images = list(result.images or [])
    flags, dropped = gprep_budget._budget_keep_flags(images)
    return _kept_specs(images, flags), dropped_note(dropped)


_dropped_note = alias(sys.modules[__name__], "dropped_note")
