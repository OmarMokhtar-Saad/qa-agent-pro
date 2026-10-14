"""Run every pin against a rendered report and the run store; never raises."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import NamedTuple

from tools.mobile import report, run_store
from tools.mobile.rchk_consts import (
    FORBIDDEN_MARKERS,
    PIN_NO_MARKERS,
    PIN_NO_SECRET,
    PIN_NO_XML,
    PIN_SIZE,
    RAW_XML,
    SECRET_TOKENS,
)
from tools.mobile.rchk_crash import _pin_capture_tier, _pin_crashes
from tools.mobile.rchk_links import _pin_links
from tools.mobile.rchk_page import _Page
from tools.mobile.rchk_pins import (
    _pin,
    _pin_absent,
    _pin_card_count,
    _pin_cases,
    _pin_script,
    _pin_terminator,
    _pin_totals,
)

# The logger keeps the original module's name, so log routing does not change.
logger = logging.getLogger("tools.mobile.report_selfcheck")


class _Seen(NamedTuple):
    """The parsed page, its raw text and its size in bytes."""

    page: _Page
    text: str
    size: int


def _store_view(run_id: str) -> tuple:
    """``(cases, store_ids, manifest)`` as the store has them."""
    cases = [
        case
        for case in (run_store.list_cases(run_id) or {}).get("content") or []
        if isinstance(case, dict)
    ]
    store_ids = [str(case.get("tc_id") or "") for case in cases]
    manifest = (run_store.read_manifest(run_id) or {}).get("content")
    return cases, store_ids, manifest if isinstance(manifest, dict) else {}


def _size_pin(size: int) -> dict:
    return _pin(
        PIN_SIZE,
        size <= report.MAX_PAGE_BYTES,
        str(size) + " bytes of " + str(report.MAX_PAGE_BYTES),
    )


def _all_pins(run_id: str, seen: _Seen) -> list:
    """Every pin, in report order, against the parsed page and the store."""
    cases, store_ids, manifest = _store_view(run_id)
    page, text, size = seen
    store_tally = report.tally(cases)
    return [
        _pin_cases(page.cards, store_ids),
        _pin_totals(page.summary, store_tally, len(cases)),
        _pin_card_count(page.cards, page.summary),
        _pin_terminator(page.end, page.cards),
        _pin_absent(PIN_NO_MARKERS, text, FORBIDDEN_MARKERS),
        _pin_absent(PIN_NO_XML, text, RAW_XML),
        _pin_script(page),
        _pin_links(page, text, report.report_path(run_id).parent),
        _pin_absent(PIN_NO_SECRET, text, SECRET_TOKENS),
        _size_pin(size),
        _pin_crashes(page.crashes, cases),
        _pin_capture_tier(manifest),
    ]


def _target(run_id: str, html_path: str) -> tuple:
    """``(path, None)``, or ``(None, the refusal)`` when there is no page."""
    target = str(html_path or "")
    if not target:
        produced = report.render(run_id)
        if produced.get("error"):
            return None, {"error": str(produced["error"]), "content": None}
        target = str((produced.get("content") or {}).get("path") or "")
    path = Path(target)
    if not path.is_file():
        return None, {"error": "No report file at " + target[:200], "content": None}
    return path, None


def _parse(text: str) -> _Page:
    page = _Page()
    page.feed(text)
    page.close()
    return page


def _content(path: Path, seen: _Seen, pins: list) -> dict:
    return {
        "ok": all(entry["ok"] for entry in pins),
        "pins": pins,
        "path": str(path),
        "bytes": seen.size,
        "cards": len(seen.page.cards),
    }


def check(run_id: str, html_path: str = "") -> dict:
    """Every pin against the page for *run_id*. Never raises.

    With no *html_path* the report is RENDERED first, so the check runs against
    the current state of the store rather than against whatever page happened to
    be lying on disk.
    """
    try:
        path, refusal = _target(run_id, html_path)
        if refusal:
            return refusal
        text = path.read_text(encoding="utf-8", errors="replace")
        size = len(text.encode("utf-8", errors="replace"))
        seen = _Seen(_parse(text), text, size)
        return {"error": None, "content": _content(path, seen, _all_pins(run_id, seen))}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.report_selfcheck.check failed")
        return {"error": str(exc), "content": None}
