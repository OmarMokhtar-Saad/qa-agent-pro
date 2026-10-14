"""Jira image gate: the unreadable-images note and the evidence a fetched ticket reveals."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from handlers.image_gate_names import _clamped_count, _safe_image_name
from handlers.image_gate_unread_notes import (
    _partial_fetch_unreadable_note,
    _zero_fetch_unreadable_note,
)

logger = logging.getLogger("tools.mcp_handlers")


# 2026-09-02: `_server_fetched_image_note` DELETED. It rendered the
# QA_JIRA_ATTACHMENT_FETCH_ENABLED disclosure from `images_fetched_server_side`,
# which nothing has written since batch D1 (2026-08-15) deleted the fetch, so
# its two tester-facing strings named a deleted flag and could never be shown.
# A revived fetch (docs/RETIRED_CAPABILITIES.md §1) writes its own note.


# The prepare-reply notice for ticket images this server could NOT read.
#
# Returns "" when there is nothing left to say. Split out of
# handle_prepare_test_cases (Batch C, C1) for two reasons: it is now
# CONDITIONAL prose rather than one fixed string, and inline it was reachable
# only through a whole prepare, so the falsehood below shipped untested.
#
# Since the R3 completeness fix a PARTIAL server-side fetch leaves
# ``images_unavailable`` SET on purpose -- that is what keeps the image gate's
# completeness arm armed -- and the old blanket wording then stated two
# falsehoods in the SAME reply: it counted every attachment as unread when some
# had just been fetched, and it promised a "TEXT only" generation directly
# under _server_fetched_image_note, which lists the screens attached to that
# very reply. So on a partial fetch the count, the names and the wording narrow
# to the REMAINDER -- matched by FILENAME against what actually arrived, not by
# position, because jira_attachments._ordered gives inline images the budget
# first and the ticket's order is therefore not the fetch order.
#
# With nothing fetched the original wording is returned unchanged. Filenames
# are ticket-supplied and untrusted, so they go through _safe_image_name
# exactly as beat 2's do (the inline version interpolated them raw).
#
# 2026-08-09 (review M2/W2, NARROWED the same day by review L2): the OTHER two
# intake channels are subtracted from the remainder, because a note calling a
# screen unreadable while it rides on the very same request is simply false.
# They are NOT equivalent evidence and are still not treated as such:
# ``captured`` is this server's own observation (it handed those bytes to the
# chat client) and rebuts the "TEXT only" claim on EVIDENCE, while ``attested``
# is host-ASSERTED with no server-side evidence at all -- it can never claim
# the ticket's own screens arrived, and never upgrades the wording to say they
# did.
#
# What L2 changed is only what this server may ASSERT AGAINST ITSELF. A flat
# "generated from the ticket TEXT only" is a claim about the WHOLE payload, and
# it is false the moment ANY image rides on the request -- so on the zero-fetch
# arm an attestation now retires that sentence too, exactly as it already
# retired the "generated WITHOUT the remaining screen(s)" clause on the partial
# arm. The weaker/stronger ordering is unchanged; the stronger claim simply no
# longer survives on the weaker branch. On EVERY arm the screens the fetch
# could not get are still NAMED. Never raises.
def _unreadable_images_note(
    url_content: object, *, attested: int = 0, captured: int = 0
) -> str:
    """Return the prepare-reply notice for ticket images this server could not read."""
    with never_raise(logger, "_unreadable_images_note failed", exc_info=True):
        return _unreadable_images_text(url_content, attested, captured)
    return ""


# Nothing was ever disclosed, so there is no unreadable image to
# report (review N2): the old inline version could only run with a
# non-empty attachment list, and a "0 image attachment(s)" paragraph
# would be a fresh falsehood.
# SERVER-OBSERVED vs HOST-ASSERTED (review W2, narrowed by L2 on
# 2026-08-09): only `captured` REBUTS a TEXT-only claim on evidence --
# only those bytes demonstrably left this server, so only they earn the
# "check they cover the same ground" wording. `attested` cannot claim
# equivalence and never will; it only stops this server ASSERTING the
# stronger negative -- that no image reached the model at all -- about a
# request whose own IMAGE_JOB describes the attachments. It joins
# `_other` for the partial-fetch remainder AND for the zero-fetch
# narrowing below.
def _unreadable_images_text(url_content: object, attested: int, captured: int) -> str:
    """Return the body of the unreadable-images note; may raise, the caller swallows."""
    uc = url_content if isinstance(url_content, dict) else {}
    names = [
        _safe_image_name(a.get("filename")) or "attachment"
        for a in (uc.get("image_attachments") or [])
        if isinstance(a, dict)
    ]
    if not names:
        return ""
    observed = _clamped_count(captured, hi=99)
    other = _clamped_count(attested, hi=99) + observed
    fetched = _clamped_count(uc.get("images_fetched_server_side"), hi=999)
    if not fetched:
        return _zero_fetch_unreadable_note(names, observed, other)
    return _partial_fetch_unreadable_note(uc, names, (fetched, other))


def _attachment_evidence(uc: dict) -> tuple:
    """Return the evidence tuple when image attachment metadata came back."""
    names = [
        _safe_image_name(a.get("filename")) or "attachment"
        for a in (uc.get("image_attachments") or [])
        if isinstance(a, dict)
    ][:8]
    return (len(names) or 1, names, "attachments")


def _embedded_evidence(uc: dict, refs: int) -> tuple:
    """Return the evidence tuple when the description embeds image refs."""
    names = [
        _safe_image_name(x)
        for x in (uc.get("description_image_labels") or [])[:8]
        if _safe_image_name(x)
    ]
    return (refs, names, "embedded")


# What the FETCHED ticket reveals about images: ``(count, names, kind)``.
#
# kind is "attachments" (image attachment metadata came back, i.e.
# images_unavailable), "embedded" (the description embeds image refs),
# "unknown" (the payload carried no `attachment` field at all -- NOT the same
# fact as "no attachments"), or "" when the ticket revealed nothing.
#
# Precedence matches the existing post-hoc notice exactly. *names* are the
# ticket's OWN filenames/labels, so beat 2 can name the screens -- passed
# through _safe_image_name because attachment FILENAMES arrive from jira_mcp
# unsanitized (see that helper). Pure, never raises.
def _ticket_image_evidence(url_content: dict | None) -> tuple:
    """Return what the fetched ticket reveals about images as (count, names, kind)."""
    with never_raise(logger, "_ticket_image_evidence failed", exc_info=True):
        uc = url_content if isinstance(url_content, dict) else {}
        if uc.get("images_unavailable"):
            return _attachment_evidence(uc)
        refs = int(uc.get("description_image_refs") or 0)
        if refs > 0:
            return _embedded_evidence(uc, refs)
        if uc.get("attachments_unknown"):
            return (0, [], "unknown")
        return (0, [], "")
    return (0, [], "")
