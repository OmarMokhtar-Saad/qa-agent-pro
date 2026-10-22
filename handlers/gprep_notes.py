"""Disclosure notes for the host-mode PREPARE reply: text-only skip and image-gap wording."""

from __future__ import annotations

import logging
import sys
from typing import NamedTuple

from config.settings_guard import never_raise
from handlers import image_gate
from handlers.seam_alias import alias

logger = logging.getLogger("tools.mcp_handlers")

_BUDGET_NOTE = ""


# "> ..." disclosure when the tester's own text-only source_plan='jira'
# pick skipped beat 2 while the ticket itself referenced screens (v1.97.0
# cursor-hardening item 2). "" when the prep's meta carries no skip stamp
# (every prep before this change, and every prep where the pick did not
# skip anything). Never raises.
def text_only_image_skip_note(meta: dict) -> str:
    """The "> ..." text-only skip disclosure, or "" when the prep has no skip stamp."""
    with never_raise(logger, "text_only_image_skip_note failed", exc_info=True):
        _n = image_gate._clamped_count(
            (meta or {}).get("text_only_image_skip_count"), lo=0, hi=99
        )
        if _n <= 0:
            return ""
        return (
            f"\n> \u2139\ufe0f {_n} screen(s) referenced by this ticket were not "
            "read -- generated from the ticket text only (the tester chose it, "
            f"or no screens or design link were provided and no device is "
            f"attached; attach screenshots to include them).{_skip_names_note(meta)}"
        )
    return ""


def _skip_names_note(meta: dict) -> str:
    """The " Names: `a`, `b`." suffix for the skipped screens, or ""."""
    _names = [str(x) for x in ((meta or {}).get("text_only_image_skip_names") or [])][
        :8
    ]
    return (" Names: " + ", ".join(f"`{n}`" for n in _names) + ".") if _names else ""


def _both_channels_what(att: int, cap: int) -> str:
    """The evidence sentence when both intake channels carried screens."""
    return (
        f"You told me {att} screenshot(s) were attached to the chat, and "
        f"this server captured {cap} device screen(s) and kept them on the "
        "server (screenshots are never sent to you; the two counts are "
        "per-CHANNEL and may describe the same physical screens)"
    )


def _captured_what(cap: int) -> str:
    """The evidence sentence when only device captures carried screens."""
    return (
        f"This server captured {cap} device screen(s) with "
        "`qa_capture_screens` and kept them on the server (screenshots are "
        "never sent to you)"
    )


def _image_gap_channels(att: int, cap: int) -> tuple[str, str]:
    """(what the image evidence was, the budget sentence) for the gap note."""
    if att and cap:
        return _both_channels_what(att, cap), _BUDGET_NOTE
    if cap:
        return _captured_what(cap), _BUDGET_NOTE
    return f"You told me {att} screenshot(s) were attached to the chat", ""


class _GapMsg(NamedTuple):
    """What the image-gap warning text is rendered from."""

    what: str
    budget: str
    readable: int
    promised: int


def _image_gap_message(g: _GapMsg) -> str:
    """The warning text: a subset came back, or no readable description did."""
    what, budget, readable, promised = g
    if readable:
        return (
            f"> ⚠️  {what}, but readable `image_descriptions` came back "
            f"for only {readable} of {promised} -- so this suite may be "
            "grounded on a SUBSET of the screens, and this server made no "
            "vision call of its own. If the screens with no description "
            "carry requirements, supply them again and prepare again."
            f"{budget}\n\n"
        )
    return (
        f"> ⚠️  {what}, but the submission came back with NO readable "
        "`image_descriptions` -- so there is NO evidence any image was "
        "actually read, and this server made no vision call of its own. "
        "Treat this suite as generated from the ticket TEXT: if those "
        "screens carry requirements, attach them again or re-capture them "
        f"with `qa_capture_screens`, then prepare again.{budget}\n\n"
    )


# Disclose IMAGE evidence that never came back -- on EITHER intake channel.
#
# 2026-08-09 (multi-image intake): "some came back" is not the same fact as
# "all of them came back". A NUMERIC shortfall -- fewer readable
# `image_descriptions` than this prep's own stamps promised -- now gets its
# own, milder note, so a 3-image prep that returned 1 description no longer
# reads as a clean submit. It is a NOTE only: nothing here refuses, and the
# QA_HOST_IMAGE_REQUIRE_RELEVANT machinery is deliberately untouched.
#
# The two channels are deliberately kept DISTINGUISHABLE in the text, because
# they carry different evidence:
#
#   * ``attached_image_count`` is host-ATTESTED. No bytes ever reached this
#     server, so IMAGE_JOB's returned ``image_descriptions`` is the only
#     verification tell there is.
#   * ``captured`` counts DEVICE screens this server itself HANDED to the chat
#     client (`qa_capture_screens`). The bytes are real here; what is missing
#     is evidence the host's model read them. Saying "you told me" about those
#     would be an over-claim in the other direction. It is deliberately NOT
#     phrased as "the host received N": the per-result byte/count budget in
#     `_budget_keep_flags` runs after the stamp and NAMES anything it drops,
#     so this note points at that disclosure instead of contradicting it.
#
# Until 2026-08-09 this bailed on ``attested <= 0``, which made the strong
# warning STRUCTURALLY UNREACHABLE on a capture-only run -- the stamp read
# ``attached_image_count: 0`` while ``host_image_job: true`` (the TICKET-5646
# defect). The two counts are PER-CHANNEL and may describe the same physical
# screens (a `jira_both` plan), which the both-channel wording says out loud
# rather than implying 2N distinct screens. Silent when nothing was attested
# OR captured and silent when descriptions did come back, so a normal submit is
# byte-identical, and ``captured`` defaults to 0 so an OLD prep envelope
# without the new stamp behaves exactly as before. Both counts are coerced
# INSIDE this try, so a garbage stamp cannot raise at the call site. Never
# raises.
def attested_image_gap_note(attested, result, *, captured=0) -> str:
    """The IMAGE-evidence gap disclosure for either intake channel; never raises."""
    with never_raise(logger, "attested_image_gap_note failed", exc_info=True):
        return _image_gap_note_text(attested, result, captured)
    return ""


def _readable_count(result) -> int:
    """How many readable image descriptions the submission came back with."""
    if not getattr(result, "ran", False):
        return 0
    return len(list(getattr(result, "images", []) or []))


def _image_gap_note_text(attested, result, captured) -> str:
    """The gap note body; may raise, its only caller swallows."""
    _att = image_gate._clamped_count(attested, lo=0, hi=99)
    _cap = image_gate._clamped_count(captured, lo=0, hi=99)
    if (_att + _cap) <= 0 or result is None:
        return ""
    _readable = _readable_count(result)
    # NUMERIC reconciliation (2026-08-09). Both sides are UNTRUSTED (host
    # list + prep stamp), so the counts are clamped before any wording sees
    # them. _promised is at least 1: the guard above saw something.
    _promised = image_gate._clamped_count(
        image_gate._int_or(_att + _cap, 1), lo=1, hi=99
    )
    if _readable >= _promised:
        return ""
    _what, _budget = _image_gap_channels(_att, _cap)
    return _image_gap_message(
        _GapMsg(what=_what, budget=_budget, readable=_readable, promised=_promised)
    )


_text_only_image_skip_note = alias(sys.modules[__name__], "text_only_image_skip_note")
_attested_image_gap_note = alias(sys.modules[__name__], "attested_image_gap_note")
