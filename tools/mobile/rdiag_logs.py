"""Diagnostics logs section: findings, the explore-lane stop reason and the device-error breakdown."""

from __future__ import annotations

from tools.mobile import run_finalize
from tools.mobile.rdiag_findings import _findings_section
from tools.mobile.report_text import esc
from tools.mobile.report_verdict import error_classes, error_phrase, explore_stop


def _logs_section(manifest: object, facts: object, turns: int) -> str:
    """Findings, the explore-lane stop reason, and the device-error breakdown.

    The breakdown reads :func:`error_classes` -- the SAME call the overview KPI reads. It
    does not count rows of its own; two derivations of one number drift, and the pin
    monkeypatches the producer and requires both to move.
    """
    book = manifest if isinstance(manifest, dict) else {}
    body = _findings_section(book, turns) or ""
    stop = explore_stop(book) or run_finalize.stop_text(book)
    if stop:
        body += '<div class="guard"><p class="gapterm">why this run stopped</p>'
        body += '<p class="gsub">' + esc(stop, 300) + "</p></div>"
    body += (
        '<div class="card"><p class="elab">device errors by class</p>'
        '<p class="logline">'
        + esc(error_phrase(error_classes(facts)), 300)
        + "</p></div>"
    )
    return body
