"""Jira image gate: the zero-fetch and partial-fetch unreadable-images notes."""

from __future__ import annotations

from typing import NamedTuple

from handlers.image_gate_names import _safe_image_name


def _zero_covered_text(names: list, observed: int) -> str:
    """Return the note when captured device screens cover every unread attachment."""
    return (
        "> \u2139\ufe0f This ticket's "
        f"{len(names)} image attachment(s) could NOT be read from "
        f"Jira by this server ({', '.join(names) or 'unnamed'}), but "
        f"{observed} device screen(s) this server captured ride on "
        "this same request \u2014 so this is NOT a TEXT-only "
        "generation. Check they cover the same ground, and attach "
        "the ticket's own screenshots if they matter."
    )


# 2026-08-09 (review L2). The STRONGER claim must not survive
# on the WEAKER branch: the partial-fetch arm already lets
# an ATTESTED image retire "generated WITHOUT the remaining
# screen(s)", so leaving the flat "TEXT only" standing here
# asserted something stronger still -- that no image reached the
# model at all -- while this very payload's IMAGE_JOB describes
# the attached screenshots. The unread attachments stay NAMED
# and the explanation is unchanged; only the claim narrows to
# what this server can actually stand behind.
def _zero_other_text(names: list, other: int) -> str:
    """Return the note when other intake channels carry images on this request."""
    return (
        "> \u2139\ufe0f This ticket has "
        f"{len(names)} image attachment(s) that could NOT be read "
        f"({', '.join(names) or 'unnamed'}): Jira is now read "
        "through your own Atlassian MCP connection, which returns "
        f"attachment metadata but not the image bytes. {other} "
        "image(s) from the other intake channels (chat attachments "
        "and/or captured device screens) ride on this same request, "
        "so this is NOT a TEXT-only generation \u2014 check they "
        "cover the same ground, and attach the ticket's own "
        "screenshot(s) if they matter."
    )


def _zero_text_only(names: list) -> str:
    """Return the note when nothing but the ticket text reaches generation."""
    return (
        "> \u2139\ufe0f This ticket has "
        f"{len(names)} image attachment(s) that could NOT be read "
        f"({', '.join(names) or 'unnamed'}): Jira is now read through "
        "your own Atlassian MCP connection, which returns attachment "
        "metadata but not the image bytes. The test cases below are "
        "generated from the ticket TEXT only \u2014 attach the "
        "screenshot(s) to this chat if they matter."
    )


def _zero_fetch_unreadable_note(names: list, observed: int, other: int) -> str:
    """Return the unreadable-images note when the server fetched none of the images."""
    if observed >= len(names):
        return _zero_covered_text(names, observed)
    if other:
        return _zero_other_text(names, other)
    return _zero_text_only(names)


class _PartialFacts(NamedTuple):
    """What the partial-fetch note needs: remainder, total, fetched, other."""

    rest: list
    total: int
    fetched: int
    other: int


def _partial_rest(uc: dict, names: list, fetched: int) -> list:
    """Return the attachment names the partial fetch did not deliver."""
    # Both sides normalise IDENTICALLY (review N3): a filename that
    # sanitizes to "" becomes "attachment" in `names`, so it must do the same
    # here or a screen that DID arrive would be reported as outstanding.
    got = {
        _safe_image_name((i or {}).get("filename")) or "attachment"
        for i in (uc.get("images") or [])
        if isinstance(i, dict)
    }
    rest = [n for n in names if n not in got] or names[fetched:]
    if len(rest) > len(names) - fetched:
        # Under-matching (duplicate or unrecognisable filenames) must never
        # let the reported remainder exceed what is actually outstanding.
        rest = names[fetched:]
    return rest


# Covered by the other channels (review M2). The FETCH still fell
# short and that stays disclosed, screens still NAMED -- what is
# dropped is the false "generated WITHOUT the remaining screen(s)"
# claim, replaced by the check the tester can actually act on.
def _partial_covered_text(facts: _PartialFacts) -> str:
    """Return the note when other channels cover the screens the fetch missed."""
    rest, total, fetched, other = facts
    return (
        "> \u2139\ufe0f "
        f"{len(rest)} of this ticket's {total} image attachment(s) "
        f"could NOT be fetched by this server ({', '.join(rest)}) "
        f"\u2014 it got {fetched} of them (named above). {other} "
        "image(s) from the other intake channels (chat attachments "
        "and/or captured device screens) ride on this same request, so "
        "the cases below are NOT generated without those screens "
        "\u2014 check they are the same ones."
    )


def _partial_text(facts: _PartialFacts) -> str:
    """Return the note naming the screens that stay unread after a partial fetch."""
    rest, total, fetched, _ = facts
    return (
        "> \u2139\ufe0f "
        f"{len(rest)} of this ticket's {total} image attachment(s) "
        f"could NOT be read ({', '.join(rest)}) \u2014 this server fetched "
        f"{fetched} of them (named above) and could not get the rest. The "
        "test cases below are generated WITHOUT the remaining screen(s) "
        "\u2014 attach those to this chat if they matter."
    )


def _partial_fetch_unreadable_note(uc: dict, names: list, counts: tuple) -> str:
    """Return the unreadable-images note for the remainder of a partial fetch."""
    fetched, other = counts
    rest = _partial_rest(uc, names, fetched)
    if not rest:
        # Everything the ticket named is attached to this reply. The fetch
        # note above already said so, and a second paragraph claiming
        # unreadable images would contradict it.
        return ""
    facts = _PartialFacts(rest, len(names), fetched, other)
    if other >= len(rest):
        return _partial_covered_text(facts)
    return _partial_text(facts)
