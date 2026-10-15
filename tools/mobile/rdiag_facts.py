"""Diagnostics run-facts strip: the device, app, lane, created, state, language and lease cells."""

from __future__ import annotations

from typing import NamedTuple

from tools.mobile.rdiag_device import _app_label, _device_sub, _locale_cell
from tools.mobile.report_text import _stamp, _text, esc
from tools.mobile.report_verdict import coverage_phrase
from tools.mobile.report_widgets import _PageData
from tools.mobile_evidence import render as ev_render


def _machine_cells(manifest: dict) -> list:
    return [
        (
            "device",
            esc(manifest.get("serial") or "(not attached)", 40),
            _device_sub(manifest),
            "",
        ),
        ("app", esc(_app_label(manifest), 80), "the package under test", ""),
        (
            "lane",
            esc(manifest.get("lane") or "suite", 24),
            "started from " + esc(manifest.get("source") or "(unrecorded)", 40),
            "",
        ),
    ]


def _created_cell(run_id: str, manifest: dict) -> tuple:
    return (
        "created",
        esc(_stamp(manifest.get("created")), 40),
        "run " + esc(run_id, 64),
        "",
    )


def _state_cell(partial: bool, coverage: dict | None) -> tuple:
    # TWO values, TWO names. The cell's VALUE is the run LIFECYCLE
    # (``partial``); its sub-line is VERDICT COVERAGE, which is a different
    # question -- a finished run can have reached no verdict at all, and
    # this cell used to claim the opposite in exactly that case. The tone
    # follows either being a gap, because either is.
    return (
        "state",
        "in progress" if partial else "finished",
        esc(coverage_phrase(coverage), 200),
        "gap" if (partial or not (coverage or {}).get("complete")) else "ok",
    )


def _run_cells(run_id: str, manifest: dict, state_cell: tuple) -> list:
    """The five always-present cells of the run facts strip."""
    return _machine_cells(manifest) + [_created_cell(run_id, manifest), state_cell]


def _holder_cell(holder: str, lease: dict) -> tuple:
    if lease.get("released_at"):
        return (
            "lease",
            "released",
            "by session " + esc(holder, 40),
            "",
        )
    return (
        "lease",
        "held by session " + esc(holder, 40),
        "heartbeat " + esc(_stamp(lease.get("heartbeat")), 40),
        "",
    )


def _displaced_cell(displaced: str) -> tuple:
    return (
        "taken over from",
        esc(displaced, 40),
        "an earlier chat that was told to stop",
        "",
    )


def _lease_cells(lease: dict) -> list:
    """The lease holder and takeover cells, each only when the lease names one."""
    holder = _text(lease.get("session_id"), 40)
    displaced = _text(lease.get("taken_over_from"), 40)
    items: list = []
    if holder:
        items.append(_holder_cell(holder, lease))
    if displaced:
        items.append(_displaced_cell(displaced))
    return items


def _extra_cells(manifest: dict, lease: dict, loaded: dict | None) -> list:
    items: list = []
    locale_cell = _locale_cell(manifest)
    if locale_cell:
        items.append(locale_cell)
    if loaded:
        items.extend(ev_render.facts_cells(loaded))
    items.extend(_lease_cells(lease))
    return items


def _fact_cell(item: tuple) -> str:
    label, value, sub, tone = item
    return (
        '<div class="rf"><span class="rfl">'
        + label
        + '</span><span class="rfv">'
        + (('<i class="sw ' + tone + '"></i>') if tone else "")
        + value
        + (("<small>" + sub + "</small>") if sub else "")
        + "</span></div>"
    )


class _FactsIn(NamedTuple):
    """What the run facts strip reads: the run, its lease and the page data."""

    run_id: str
    manifest: dict
    lease: dict
    partial: bool
    page: _PageData = _PageData()


def _facts_strip(facts: _FactsIn) -> str:
    page = facts.page
    items = _run_cells(
        facts.run_id, facts.manifest, _state_cell(facts.partial, page.coverage)
    )
    items.extend(_extra_cells(facts.manifest, facts.lease, page.loaded))
    cells = "".join(_fact_cell(item) for item in items)
    return '<div class="runstrip" aria-label="Run facts">' + cells + "</div>"
