"""Diagnostics performance section: the timing tiles, the per-case figure and its legend."""

from __future__ import annotations

from collections import Counter

from tools.mobile.report_text import LAT_BUCKETS, _percentile, esc, fmt_ms
from tools.mobile.report_widgets import _Kpi, _kpi
from tools.mobile_evidence import render as ev_render

_WHY_ACTION = (
    '<details class="kwhy"><summary>what this means</summary><p>measured by the replay '
    "clock around one action — the tap or the wait itself, not the model's planning turn, "
    "which happens in the tester's own chat</p></details>"
)
_WHY_CASE = (
    '<details class="kwhy"><summary>what this means</summary><p>the sum of that case\'s '
    "measured actions, a FLOOR rather than the whole truth: the turns the model spent "
    "planning between actions are nobody's measurement here</p></details>"
)


def _perf_tile(values: list, title: str, tail: str) -> str:
    """One KPI tile: the median, the spread and the count of ``values``."""
    return _kpi(
        _Kpi(
            esc(fmt_ms(_percentile(values, 0.5)), 16),
            title,
            '<span class="kstat">usually '
            + esc(fmt_ms(_percentile(values, 0.5)), 16)
            + ", between "
            + esc(fmt_ms(min(values)), 16)
            + " and "
            + esc(fmt_ms(max(values)), 16)
            + '</span><span class="kstat">across '
            + str(len(values))
            + tail,
        )
    )


def _perf_tiles(step_ms: list, walls: list) -> list:
    tiles = [
        _perf_tile(step_ms, "How long an action took", " actions</span>" + _WHY_ACTION),
    ]
    if walls:
        tiles.append(
            _perf_tile(
                [w for _f, w in walls],
                "How long a case took",
                " cases</span>" + _WHY_CASE,
            )
        )
    return tiles


def _perf_run_rows(walls: list) -> str:
    top = max(w for _f, w in walls) or 1
    ordered = sorted(walls, key=lambda pair: -pair[1])
    return "".join(
        '<li class="runrow"><button type="button" data-jump-case="'
        + esc(f["slug"], 40)
        + '"><span class="rname"><b>'
        + esc(f["tc_id"], 40)
        + "</b>"
        + (esc(f["title"], 80) or "(untitled case)")
        + '</span><span class="rval">'
        + esc(fmt_ms(w), 16)
        + '</span><span class="rtrack"><i class="sw-lat-'
        + (f["lat"] or "fast")
        + '" style="width:%.1f%%"></i></span></button></li>'
        % max(1.0, w / float(top) * 100.0)
        for f, w in ordered
    )


def _perf_legend(walls: list) -> str:
    buckets = Counter(f["lat"] for f, _w in walls)
    return "".join(
        '<li><button type="button" data-jump-group="lat" data-jump-value="'
        + key
        + '"><i class="sw sw-lat-'
        + key
        + '"></i><b>'
        + str(buckets.get(key, 0))
        + "</b>"
        + label
        + "</button></li>"
        for key, label, _ceiling in LAT_BUCKETS
    )


def _perf_figure(walls: list) -> str:
    return (
        '<figure class="hist"><figcaption><b>How long a case took</b><span>'
        + str(len(walls))
        + " case"
        + ("" if len(walls) == 1 else "s")
        + ', each one shown · click one to open it</span></figcaption><ul class="runs">'
        + _perf_run_rows(walls)
        + '</ul><ul class="seglegend">'
        + _perf_legend(walls)
        + "</ul></figure>"
    )


def _perf(facts: list, loaded: dict | None = None) -> str:
    step_ms = [row["ms"] for f in facts for row in f["rows"] if row["ms"] is not None]
    walls = [(f, f["wall"]) for f in facts if f["wall"] is not None]
    if not step_ms:
        return (
            '<p class="empty-note big">Nothing here carried a measured duration yet — the tiles '
            "and the figure appear once an action has been replayed.</p>"
        ) + (ev_render.perf_extra(loaded) if loaded else "")
    tiles = _perf_tiles(step_ms, walls)
    figure = _perf_figure(walls) if walls else ""
    return (
        '<p class="hint">Every action the server replays is timed on its own clock, so these are '
        "real elapsed times — for the action alone. The model's planning between actions runs in "
        "the tester's chat and is not measured here.</p>"
        '<div class="kpis">'
        + "".join(tiles)
        + "</div>"
        + figure
        + (ev_render.perf_extra(loaded) if loaded else "")
    )
