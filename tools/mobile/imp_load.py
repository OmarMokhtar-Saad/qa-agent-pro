"""Dispatch a source to its importer, and render the rejected rows."""

from __future__ import annotations

import logging
from pathlib import Path

from tools.mobile.imp_csv import from_csv
from tools.mobile.imp_md import from_markdown
from tools.mobile.imp_source import _maybe_path
from tools.mobile.imp_text import fail
from tools.mobile.imp_xlsx import from_xlsx
from tools.untrusted import single_line

logger = logging.getLogger("tools.mobile.importers")

_NOT_A_TABLE = (
    "That does not look like a markdown table, a .csv or an .xlsx "
    "path, so nothing was imported."
)


def _load_path(candidate: Path) -> dict:
    suffix = candidate.suffix.lower()
    if suffix == ".xlsx":
        return from_xlsx(candidate)
    if suffix == ".csv":
        return from_csv(candidate)
    if suffix in (".md", ".markdown", ".txt"):
        return from_markdown(candidate.read_text(encoding="utf-8", errors="replace"))
    return fail(
        "Cannot import "
        + candidate.name
        + ": paste a markdown table, or give a .csv or .xlsx path."
    )


def _load_text(text: str) -> dict:
    if "|" in text:
        return from_markdown(text)
    if "," in text and "\n" in text:
        return from_csv(text)
    return fail(_NOT_A_TABLE)


def load(source: object) -> dict:
    """Dispatch by extension, then by content. ``{"error", "content": {...}}``."""
    try:
        text = str(source or "")
        candidate = _maybe_path(text)
        if candidate is not None:
            return _load_path(candidate)
        return _load_text(text)
    except OSError as exc:
        return fail("Could not read that source: " + str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.importers.load failed")
        return fail(str(exc))


def _rejection_line(item: dict) -> str:
    return (
        "- row "
        + str(item.get("row"))
        + ": "
        + single_line(item.get("why") or "", 160)
        + (
            " -- " + single_line(item.get("title"), 60)
            if str(item.get("title") or "")
            else ""
        )
    )


def _rejected_block(rejected: list) -> str:
    lines = [
        "**"
        + str(len(rejected))
        + " row(s) were NOT imported.** Fix and re-paste these:",
    ]
    lines.extend(_rejection_line(item) for item in rejected[:50])
    return "\n".join(lines)


def render_rejections(content: object) -> str:
    """The rejected rows as a tester-facing block. ``""`` when there are none."""
    body = content if isinstance(content, dict) else {}
    if isinstance(body.get("content"), dict):
        body = body["content"]
    rejected = list(body.get("rejected") or [])
    if not rejected:
        return ""
    return _rejected_block(rejected)
