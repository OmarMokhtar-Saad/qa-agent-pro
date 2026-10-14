"""Rendering saved notes for the host model, always as untrusted text."""

from __future__ import annotations

from typing import Any

from tools.mobile import appk_consts
from tools.untrusted import wrap_untrusted


def echo(note: dict) -> str:
    """One note as UNTRUSTED text for the host model."""
    label = "app note #%s (%s)" % (note.get("id"), note.get("kind"))
    if note.get("status") == "needs_recheck":
        label += " unverified@%s" % (note.get("recheck_at") or "")
    return wrap_untrusted(
        label,
        str(note.get("text") or ""),
        appk_consts.MAX_NOTE_CHARS + 200,
    )


def _render_line(item: dict) -> str:
    """One list row: id, kind, text, tallies, env and status marks."""
    when = item.get("when") or {}
    line = "#%s [%s] %s (confirmed %s, contradicted %s)" % (
        item.get("id"),
        item.get("kind"),
        item.get("text"),
        item.get("confirmed", 0),
        item.get("contradicted", 0),
    )
    if when.get("env"):
        line += " -- env-scoped: not applied yet"
    if item.get("status") not in (None, "active"):
        line += " [%s]" % item["status"]
    return line


def render(package: str, notes: Any) -> str:
    """A list reply. Note text is wrapped as untrusted; env-scoped notes say so."""
    items = list(notes or [])
    if not items:
        return "No app notes saved for `%s`." % package
    total = len(items)
    items = items[-appk_consts.MAX_LIST_ROWS :]
    lines = [_render_line(item) for item in items]
    body = wrap_untrusted(
        "app notes for " + package,
        "\n".join(lines),
        appk_consts.MAX_LIST_ROWS * (appk_consts.MAX_NOTE_CHARS + 120),
    )
    count = "%d" % total
    if total > len(items):
        count = "showing the newest %d of %d" % (len(items), total)
    return (
        "App notes for `%s` (%s):\n%s\nRetire one with "
        'qa_mobile_notes(action="retire", note_id=N).' % (package, count, body)
    )
