"""Provenance summary and caveats for an atomic checklist."""

from __future__ import annotations

import logging
from typing import Any

from tools.atomic_checklist_source import (
    PROVENANCE_LIMITATION,
    UNATTRIBUTED,
    is_comment_derived,
)

logger = logging.getLogger("tools.atomic_checklist")


def _tally(out: dict[str, Any], it: object) -> None:
    source = getattr(it, "source", "") or UNATTRIBUTED
    out["counts"][source] = out["counts"].get(source, 0) + 1
    if is_comment_derived(source):
        out["comment_derived_ids"].append(getattr(it, "item_id", ""))
    elif source == UNATTRIBUTED:
        out["unattributed_ids"].append(getattr(it, "item_id", ""))


# Which requirements came from where. Never raises.
#
# ``comment_derived_ids`` is the security-relevant bucket: those requirements
# originate in Jira COMMENT text, which is attacker-writable, so every
# renderer must say so rather than presenting them as approved spec.
def provenance_summary(items: list) -> dict:
    """Which requirements came from where. Never raises."""
    out: dict[str, Any] = {
        "counts": {},
        "comment_derived_ids": [],
        "unattributed_ids": [],
    }
    try:
        for it in items or []:
            _tally(out, it)
        return out
    except Exception:
        logger.exception("provenance_summary failed — returning an empty summary")
        return {"counts": {}, "comment_derived_ids": [], "unattributed_ids": []}


def _shown(ids: list) -> str:
    return ", ".join(ids[:12]) + (" …" if len(ids) > 12 else "")


def _comment_caveat(ids: list) -> str:
    shown = _shown(ids)
    return (
        f"**Provenance warning — {len(ids)} requirement(s) "
        f"({shown}) were derived from ticket COMMENTS, not from the "
        "approved description or acceptance criteria. Jira comments are "
        "writable by anyone with ticket access and were treated as "
        "untrusted data, never as instructions. Confirm them against the "
        "approved specification before treating them as requirements.**"
    )


def _unattributed_caveat(ids: list) -> str:
    shown = _shown(ids)
    return (
        f"{len(ids)} requirement(s) ({shown}) carry no recognised "
        "source tag — they are model-inferred, or their reported source "
        "did not match the provenance allowlist. Audit them manually."
    )


def _has_items(items: list) -> bool:
    if not items:
        return False
    # list() so a non-iterable (the never-raise contract's garbage input)
    # raises HERE and degrades to [] instead of returning a lone caveat.
    return bool(list(items))


# Markdown blockquote caveats for the provenance of a checklist.
#
# ALWAYS leads with ``PROVENANCE_LIMITATION`` when there is at least one item:
# the tags are self-reported, so a block that only spoke up for comment-tagged
# items would imply the untagged ones had been verified. Further caveats name
# the comment-derived and unattributed requirements. Returns [] for an empty
# checklist. Never raises.
def provenance_caveats(items: list) -> list:
    """Markdown blockquote caveats for the provenance of a checklist."""
    try:
        if not _has_items(items):
            return []
        summary = provenance_summary(items)
        # UNCONDITIONAL, and first: everything below it is self-reported.
        out: list = [PROVENANCE_LIMITATION]
        comment_ids = summary.get("comment_derived_ids") or []
        if comment_ids:
            out.append(_comment_caveat(comment_ids))
        unattributed = summary.get("unattributed_ids") or []
        if unattributed:
            out.append(_unattributed_caveat(unattributed))
        return out
    except Exception:
        logger.exception("provenance_caveats failed — returning no caveats")
        return []
