"""Jira image gate: the BEAT 2 text for a partial and for a no-image intake."""

from __future__ import annotations

from handlers.image_gate_labels import _IMAGE_GATE_LINE, _MARK_UNNAMED_REMAINDER

_PARTIAL_HEAD = (
    "## ⏸️ One decision before I generate: the REST of the ticket's screens\n\n"
)
_PARTIAL_ASK = (
    "Ask the user, then call the SAME tool again with the SAME "
    "`feature_or_url` AND the SAME `jira_content_json` (do NOT "
    "fetch the ticket again) plus ONE of:\n\n"
    "1. **Attach the missing screens** -- attach them (Claude "
    "Code: drag the file into this chat or use `/attach`; "
    "Cursor: click the paperclip icon and pick the file), then "
    "pass `attached_image_count=<the TOTAL now attached, not "
    "just the new ones>`. They stay in YOUR context: no image "
    "bytes are sent to this server, and the generation payload "
    "will ask you to describe them.\n"
    "2. **Generate from the screens I already have** -- ASK THE "
    "TESTER FIRST; only if they agree, pass `image_gate_ack=true`. "
    "I then show the tester a confirmation dialog and only THEIR "
    "'skip the screens' pick clears this gate. The cases will "
    "reflect only those "
)
_PARTIAL_TAIL = (
    "Nothing has been prepared yet, so this costs no re-fetch and no second generation."
)
_FULL_HEAD = "## ⏸️ One decision before I generate: the ticket's screens\n\n"
_FULL_OPTIONS = (
    "Ask the user which they want, then call the SAME tool again with "
    "the SAME `feature_or_url` AND the SAME `jira_content_json` (do NOT "
    "fetch the ticket again) plus ONE of:\n\n"
    "1. **Attach the screens to this chat** -- attach them (Claude "
    "Code: drag the file into this chat or use `/attach`; Cursor: "
    "click the paperclip icon and pick the file), then pass "
    "`attached_image_count=<how many you attached>`. They stay in YOUR "
    "context: no image bytes are sent to this server, and the generation "
    "payload will ask you to describe them.\n"
    "2. **Capture them from a connected device** -- call "
    "`qa_capture_screens` first, then pass its `capture_ids`.\n"
    "3. **Generate from the ticket TEXT anyway** -- ASK THE TESTER "
    "FIRST; only if they agree, pass `image_gate_ack=true`. I then "
    "show the tester a confirmation dialog and only THEIR 'skip the "
    "screens' pick clears this gate. The cases will not reflect "
    "anything that exists only in those screens, and the reply will "
    "say so.\n\n"
    "Nothing has been prepared yet, so this costs no re-fetch and no "
    "second generation."
)


"""The 'what is still outstanding' clause of the PARTIAL beat-2 text."""


# PARTIAL intake: say the ratio out loud and name what is still
# outstanding. WHICH screens arrived is unknowable -- the chat
# channel is a COUNT, not a manifest -- so the missing list is
# offered in the ticket's own order with that caveat attached.
# Names already came through _safe_image_name in
# _ticket_image_evidence and are capped there at 8.
# 2026-08-31 (C13): a ticket can claim more images than it NAMES --
# embedded refs outnumber parsed labels, or the label list was capped
# at 8. The ratio was still quoted while the outstanding clause went
# empty, so the tester was told "2 of 5" and nothing about the 3.
def _partial_outstanding_clause(want: int, have: int, names: list) -> str:
    """Return the what-is-still-outstanding clause of the partial beat 2 text."""
    _missing = [n for n in (names or []) if n][have:][:8]
    if _missing:
        return (
            " In the ticket's own order that leaves "
            + ", ".join(f"`{n}`" for n in _missing)
            + " -- if the ones you have are different, say so."
        )
    if want > have:
        return (
            f" The ticket {_MARK_UNNAMED_REMAINDER} "
            f"{want - have}, so send whichever ones you have not "
            "already given me."
        )
    return ""


def _second_beat_partial(counts: tuple, names: list, named: str) -> str:
    """Return beat 2 text for a partial intake: some screens arrived, not all."""
    want, have = counts
    return (
        _PARTIAL_HEAD + f"> ℹ️ This ticket has {want} image(s) I could NOT read, and "
        f"you gave me {have} of {want}."
        + named
        + _partial_outstanding_clause(want, have, names)
        + " "
        + _IMAGE_GATE_LINE
        + "\n\n"
        + _PARTIAL_ASK
        + f"{have}, and the reply will say so.\n\n"
        + _PARTIAL_TAIL
    )


def _full_head(count: int, kind: str) -> str:
    """Return the opening sentence of the no-image beat 2 text."""
    if kind == "attachments":
        return f"This ticket has {count} image attachment(s) I could NOT read."
    return (
        f"This ticket's description embeds {count} image(s) -- UI mockups "
        "or screens -- that I could NOT read."
    )


def _full_promised(plan: str) -> str:
    """Return the sentence noting an image plan was picked but nothing arrived."""
    if plan in ("jira_attach", "jira_device", "jira_both", "device"):
        return " You picked a plan that included images, but none of them reached me."
    return ""


def _second_beat_full(head: str, plan: str, named: str) -> str:
    """Return beat 2 text when no image arrived at all."""
    return (
        _FULL_HEAD
        + "> \u2139\ufe0f "
        + head
        + named
        + _full_promised(plan)
        + " "
        + _IMAGE_GATE_LINE
        + "\n\n"
        + _FULL_OPTIONS
    )
