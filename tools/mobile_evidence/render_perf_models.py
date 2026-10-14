"""The model-mix figure of the performance section."""

from __future__ import annotations

from collections import Counter
from typing import Any, NamedTuple

from tools.mobile_evidence import model
from tools.mobile_evidence import render_esc as esc


class _Lat(NamedTuple):
    bucket: str
    usd: float | None
    p50: Any
    top: float


def _meta(m: dict, usd: float | None) -> str:
    return (
        str(m["calls"])
        + " calls · "
        + format(int(m["in"]), ",")
        + " tokens in, "
        + format(int(m["out"]), ",")
        + " out"
        + (
            (" · " + esc.ev_esc(model.money(usd), 24))
            if usd is not None
            else " · unpriced"
        )
    )


def _perf_model_row(m: dict, lat: _Lat) -> str:
    bucket, usd, p50, top = lat
    return (
        '<li class="runrow"><span class="rstatic"><span class="rname"><b></b><code>'
        + esc.ev_esc(m["model"], 80)
        + '</code><span class="rmeta">'
        + _meta(m, usd)
        + '</span></span><span class="rval">'
        + esc.ev_esc(esc.fmt(p50), 16)
        + '</span><span class="rtrack"><i class="sw-lat-'
        + bucket
        + '" style="width:%.1f%%"></i></span></span></li>'
        % (max(1.0, (p50 or 0) / float(top) * 100.0))
    )


def _legend(buckets: Counter) -> str:
    return "".join(
        '<li><span class="legkey"><i class="sw sw-lat-'
        + key
        + '"></i><b>'
        + str(buckets.get(key, 0))
        + "</b>"
        + label
        + "</span></li>"
        for key, label, _c in model.LAT_BUCKETS
    )


def _rows(models: list, rates: Any) -> tuple[str, Counter]:
    top = max((model.pct(sorted(m["samples"]), 0.5) or 0) for m in models) or 1.0
    items = ""
    buckets: Counter = Counter()
    for m in sorted(models, key=lambda m: -m["calls"]):
        p50 = model.pct(sorted(m["samples"]), 0.5)
        usd = model.price(m["model"], model.TokenCount(m["in"], m["out"]), rates)
        bucket = model.lat_bucket(p50) or "fast"
        buckets[bucket] += 1
        items += _perf_model_row(m, _Lat(bucket, usd, p50, top))
    return items, buckets


def perf_models_figure(models: list, rates: Any) -> str:
    items, buckets = _rows(models, rates)
    legend = _legend(buckets)
    return (
        '<figure class="hist"><figcaption><b>Which models were used</b><span>'
        + str(len(models))
        + " model"
        + ("" if len(models) == 1 else "s")
        + " · "
        + str(sum(m["calls"] for m in models))
        + ' calls · busiest first</span></figcaption><ul class="runs">'
        + items
        + '</ul><ul class="seglegend">'
        + legend
        + "</ul></figure>"
    )
