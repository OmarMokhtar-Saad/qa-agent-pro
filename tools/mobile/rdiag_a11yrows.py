"""Diagnostics accessibility rows: one screen's table rows and the tally of what it adds."""

from __future__ import annotations

from collections import Counter

from tools.mobile import screen_audit
from tools.mobile.report_text import esc


def _a11y_note_row(screen_id: str, cap: str, note: str) -> str:
    return (
        '<tr><td class="num">'
        + esc(screen_id, 40)
        + '</td><td class="cap">'
        + cap
        + '</td><td dir="auto">'
        + esc(note, 400)
        + "</td></tr>"
    )


def _a11y_finding_row(screen_id: str, finding: dict) -> str:
    kind = str(finding.get("kind") or "")
    detail = str(finding.get("detail") or "")
    name = str(finding.get("name") or "")
    return (
        '<tr><td class="num">'
        + esc(screen_id, 40)
        + '</td><td class="cap">'
        + esc(screen_audit.KIND_LABELS.get(kind, kind), 80)
        + '</td><td dir="auto">'
        + (("<b>" + esc(name, 120) + "</b> — ") if name else "")
        + esc(detail, screen_audit.MAX_FINDING_CHARS)
        + ' <span class="cap">'
        + esc(" ".join(str(v) for v in (finding.get("ids") or [])), 120)
        + "</span></td></tr>"
    )


def _a11y_rows(screen_id: str, result: dict) -> str:
    """One screen's findings as table rows, or one honest row saying why not."""
    if not result.get("auditable"):
        note = result.get("note") or screen_audit.NO_NODES_NOTE
        return _a11y_note_row(screen_id, "nothing to audit", note)
    findings = [f for f in (result.get("findings") or []) if isinstance(f, dict)]
    if not findings:
        return _a11y_note_row(screen_id, "no finding", result.get("note") or "")
    return "".join(_a11y_finding_row(screen_id, finding) for finding in findings)


def _tally_screen(result: dict, counts: Counter) -> None:
    if result.get("auditable"):
        counts["audited"] += 1
        if not result.get("complete"):
            counts["partial"] += 1
        if result.get("density_source") != screen_audit.DENSITY_DEVICE:
            counts["assumed"] += 1
    else:
        counts["unauditable"] += 1
    counts["total"] += len(
        [f for f in (result.get("findings") or []) if isinstance(f, dict)]
    )


def _a11y_screen_row(screen_id: str, result: object, counts: Counter) -> str:
    """One screen's table rows, tallying what it adds into ``counts``."""
    if not isinstance(result, dict) or not result.get("present"):
        counts["unaudited"] += 1
        return (
            '<tr><td class="num">'
            + esc(screen_id, 40)
            + '</td><td class="cap">not audited</td><td dir="auto">'
            + "this screen was stored before the accessibility audit existed, "
            "so no finding here is evidence about it" + "</td></tr>"
        )
    _tally_screen(result, counts)
    return _a11y_rows(screen_id, result)
