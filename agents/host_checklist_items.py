"""Host mode: clean and admit host-authored checklist entries (split from host_checklist.py)."""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from agents.host_checklist_result import (
    _CL_MAX_TEXT_CHARS,
    _CL_MIN_TEXT_CHARS,
    _CL_URL_RE,
    HostChecklistResult,
)
from config.settings_guard import never_raise
from tools.atomic_checklist import EARS_PATTERNS, ChecklistItem, normalize_source


def _cl_clean(text: object) -> str:
    """Sanitize one host-authored requirement for display + downstream reuse.

    URLs are stripped for the same reason ``_ac_clean`` strips them: this text is
    derived from _GUARD-wrapped ticket material that host mode deliberately places
    in the host's context, it comes back as a REQUIREMENT, and it is rendered into
    a report and an exported spreadsheet -- it must never be able to plant a
    navigation target. Whitespace (including newlines and control characters)
    collapses so one item cannot forge extra rows.
    """
    with never_raise():
        s = _CL_URL_RE.sub("[link removed]", str(text or ""))
        s = re.sub(r"\s+", " ", s).strip()
        return s[:_CL_MAX_TEXT_CHARS]
    return ""


class ChecklistEntry(NamedTuple):
    """One host entry split into its raw fields."""

    text: object
    pattern: object
    source: Any
    had_id: bool


def _split_entry(entry) -> ChecklistEntry | None:
    """``entry`` as a ChecklistEntry, or None when it is neither a string nor a dict."""
    if isinstance(entry, str):
        return ChecklistEntry(entry, "", "", False)
    if not isinstance(entry, dict):
        return None
    text = entry.get("text") or entry.get("item") or entry.get("description") or ""
    pattern = entry.get("ears_pattern") or ""
    source = entry.get("source") or ""
    return ChecklistEntry(
        text, pattern, source, bool(entry.get("item_id") or entry.get("id"))
    )


def _admit(entry, seen: set, res: HostChecklistResult) -> ChecklistEntry | None:
    """The cleaned entry when it is usable and new, else None (counting drops)."""
    raw = _split_entry(entry)
    if raw is None:
        res.dropped += 1
        return None
    text = _cl_clean(raw.text)
    if len(text) < _CL_MIN_TEXT_CHARS:
        res.dropped += 1
        return None
    key = " ".join(text.lower().split())
    if key in seen:
        return None
    seen.add(key)
    if raw.had_id:
        res.renumbered += 1
    return raw._replace(text=text)


def _checklist_item(number: int, entry: ChecklistEntry):
    """The positionally numbered ``ChecklistItem`` for one admitted entry."""

    tag = str(entry.pattern or "").strip().lower().replace("-", "_")
    if tag not in EARS_PATTERNS:
        tag = "ubiquitous"
    return ChecklistItem(
        item_id=f"CL-{number:03d}",
        text=str(entry.text),
        ears_pattern=tag,
        source=normalize_source(entry.source),
    )


def _build_checklist_items(entries: list, res: HostChecklistResult) -> list:
    """Usable, distinct entries as positionally numbered ``ChecklistItem``s.

    Counts unusable entries into ``res.dropped`` and host-supplied ids (all
    discarded) into ``res.renumbered``.
    """
    seen: set = set()
    out: list = []
    for entry in entries:
        got = _admit(entry, seen, res)
        if got is not None:
            out.append(_checklist_item(len(out) + 1, got))
    return out
