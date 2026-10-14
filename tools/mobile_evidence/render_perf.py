"""The performance section: latency tiles, slow endpoints, and the model mix."""

from __future__ import annotations

from typing import Any

from tools.mobile_evidence import evidence, model
from tools.mobile_evidence import render_esc as esc
from tools.mobile_evidence.render_block import Kpi, SecHead, gap_kpi, kpi, sec_block
from tools.mobile_evidence.render_html import empty
from tools.mobile_evidence.render_perf_models import perf_models_figure
from tools.mobile_evidence.render_view import content_of, has_evidence

_ENDPOINT_HEAD = (
    '<p class="hint">Sort any column. Typical is the middle time; slower runs is the time 19 calls in 20 came in under (p50 and p95).</p>'
    '<div class="tablewrap"><table class="cov sortable" id="perfendpoints"><thead><tr>'
    '<th scope="col" data-sort="path">Endpoint</th><th scope="col" data-sort="calls" class="num">Calls</th>'
    '<th scope="col" data-sort="p50" class="num">Typical</th><th scope="col" data-sort="p95" class="num">Slower runs</th>'
    '<th scope="col" data-sort="worst" class="num">Slowest</th><th scope="col" data-sort="err" class="num">Errors</th>'
    "</tr></thead><tbody>"
)


def _precisely(ordered: list) -> str:
    p50, p90, p99 = (
        model.pct(ordered, 0.5),
        model.pct(ordered, 0.9),
        model.pct(ordered, 0.99),
    )
    if len(ordered) >= 8:
        return (
            "Precisely: p50 "
            + esc.ev_esc(esc.fmt(p50), 16)
            + ", p90 "
            + esc.ev_esc(esc.fmt(p90), 16)
            + ", p99 "
            + esc.ev_esc(esc.fmt(p99), 16)
        )
    return (
        str(len(ordered))
        + " measurements, too few for a percentile to mean anything: in full, "
        + ", ".join(esc.ev_esc(esc.fmt(s), 16) for s in ordered)
    )


def _series_detail(ordered: list, unit: str) -> str:
    p50 = model.pct(ordered, 0.5)
    return (
        '<span class="kstat">usually '
        + esc.ev_esc(esc.fmt(p50), 16)
        + ", between "
        + esc.ev_esc(esc.fmt(ordered[0]), 16)
        + " and "
        + esc.ev_esc(esc.fmt(ordered[-1]), 16)
        + '</span><span class="kstat">across '
        + str(len(ordered))
        + " "
        + esc.ev_esc(unit, 24)
        + "</span>"
        '<details class="kwhy"><summary>what this means</summary><p>'
        + esc.ev_esc(evidence.DERIVED_NOTE, 400)
        + "</p><p>"
        + _precisely(ordered)
        + "</p></details>"
    )


def _series_tile(series: tuple, samples: list) -> str:
    key, label, unit = series
    if not samples:
        return gap_kpi(
            label,
            "no " + unit + " carried a derivable duration in this capture",
            key=key,
        )
    ordered = sorted(samples)
    p50 = model.pct(ordered, 0.5)
    detail = _series_detail(ordered, unit)
    return kpi(Kpi(esc.ev_esc(esc.fmt(p50), 16), label, detail, key))


def _endpoint_attrs(r: dict, stats: tuple) -> str:
    p50, p95, worst = stats
    return (
        '<tr tabindex="0" data-spath="'
        + esc.ev_esc(r["path"], 160)
        + '" data-scalls="'
        + str(r["calls"])
        + '" data-sp50="'
        + (str(p50) if p50 is not None else "-1")
        + '" data-sp95="'
        + (str(p95) if p95 is not None else "-1")
        + '" data-sworst="'
        + (str(worst) if worst is not None else "-1")
        + '" data-serr="'
        + str(r["errors"])
        + '">'
    )


def _endpoint_cells(r: dict, stats: tuple) -> str:
    p50, p95, worst = stats
    return (
        '<td class="uc"><span class="cap">'
        + esc.ev_esc(r["verb"], 12)
        + "</span> <code>"
        + esc.ev_esc(r["path"], 160)
        + '</code></td><td class="num">'
        + str(r["calls"])
        + '</td><td class="num">'
        + esc.ev_esc(esc.fmt(p50), 16)
        + '</td><td class="num">'
        + esc.ev_esc(esc.fmt(p95), 16)
        + '</td><td class="num">'
        + esc.ev_esc(esc.fmt(worst), 16)
        + '</td><td class="num">'
        + (str(r["errors"]) if r["errors"] else '<span class="mt">none</span>')
        + "</td></tr>"
    )


def _perf_endpoint_row(r: dict) -> str:
    ordered = sorted(r["samples"])
    stats = (
        model.pct(ordered, 0.5),
        model.pct(ordered, 0.95),
        (ordered[-1] if ordered else None),
    )
    return _endpoint_attrs(r, stats) + _endpoint_cells(r, stats)


def _perf_endpoints_block(endpoints: list) -> str:
    rows = "".join(
        _perf_endpoint_row(r)
        for r in sorted(
            endpoints, key=lambda r: -(model.pct(sorted(r["samples"]), 0.5) or 0)
        )
    )
    return sec_block(
        "Which backend calls were slow",
        _ENDPOINT_HEAD + rows + "</tbody></table></div>",
        SecHead(note="every endpoint the run reached, slowest first"),
    )


def _perf_tail(ev: Any, content: dict) -> str:
    out = ""
    endpoints = ev.endpoints()
    if endpoints:
        out += _perf_endpoints_block(endpoints)
    models = ev.models()
    if models:
        out += perf_models_figure(models, content.get("rates"))
    return out


def perf_extra(loaded: object) -> str:
    content = content_of(loaded)
    if not has_evidence(loaded):
        return empty(
            "no app log captured, so nothing the app did could be timed: "
            + str(content.get("reason") or "")
        )
    ev = content["evidence"]
    spans = ev.spans()
    tiles = "".join(
        _series_tile(series, spans.get(series[0]) or []) for series in esc.SERIES
    )
    return (
        '<p class="hint">' + esc.ev_esc(evidence.DERIVED_NOTE, 400) + "</p>"
        '<div class="kpis">' + tiles + "</div>"
    ) + _perf_tail(ev, content)
