"""The filter toolbar of the cases section: verdict, module, priority, type and latency chips, sort and search."""

from __future__ import annotations

from collections import Counter

from tools.mobile.report_text import LAT_BUCKETS
from tools.mobile.report_verdict import TILES, _tone
from tools.mobile.report_widgets import _all_chip, _Chip, _chip, _filter_group
from tools.mobile_evidence import render as ev_render


def _verdict_chips(verdicts: Counter, ordered: list) -> str:
    return "".join(
        _chip(_Chip("verdict", name, _tone(name)[3], verdicts[name], _tone(name)[0]))
        for name in ordered
    )


def _verdict_group(facts: list) -> tuple:
    """The Verdict filter group: the tiles' order first, then any other verdict."""
    verdicts = Counter(f["verdict"] for f in facts)
    ordered = [name for name in TILES if verdicts.get(name)] + sorted(
        name for name in verdicts if name not in TILES
    )
    return (
        "Verdict",
        _all_chip("area", len(facts)) + _verdict_chips(verdicts, ordered),
    )


def _common_chips(group: str, counter: Counter) -> str:
    """One chip per value of *group*, most common first."""
    return "".join(
        _chip(_Chip(group, name, name, n)) for name, n in counter.most_common()
    )


def _latency_chips(lats: Counter) -> str:
    """One chip per latency bucket that has a case in it, in bucket order."""
    return "".join(
        _chip(_Chip("lat", key, label, lats[key], "lat-" + key))
        for key, label, _c in LAT_BUCKETS
        if lats.get(key)
    )


def _toolbar_groups(facts: list) -> list:
    counts = {
        key: Counter(f[key] for f in facts) for key in ("module", "priority", "type")
    }
    lats = Counter(f["lat"] for f in facts if f["lat"])
    return [
        _verdict_group(facts),
        ("Module", _common_chips("module", counts["module"])),
        ("Priority", _common_chips("priority", counts["priority"])),
        ("Type", _common_chips("type", counts["type"])),
        ("Latency", _latency_chips(lats)),
    ]


def _toolbar_sorts(loaded: dict | None) -> str:
    sorts = (
        ("order", "case order"),
        ("steps", "actions, most first"),
        ("wall", "replay time, longest first"),
        ("plans", "LLM turns, most first"),
        ("escapes", "escape-hatch turns, most first"),
    ) + (tuple(ev_render.extra_sorts(loaded)) if loaded else ())
    return "".join(
        '<option value="' + k + '">' + label + "</option>" for k, label in sorts
    )


def _toolbar(facts: list, loaded: dict | None = None) -> str:
    groups = _toolbar_groups(facts)
    if loaded:
        groups += ev_render.toolbar_groups(loaded, facts)
    opts = _toolbar_sorts(loaded)
    fgs = "".join(_filter_group(label, chips) for label, chips in groups)
    total = str(len(facts))
    return (
        '\n    <div class="toolbar" id="toolbar">\n      <div class="tb-row">\n        <label class="search">\n'
        '          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><circle cx="7" cy="7" r="4.5"/><path d="m10.5 10.5 3 3"/></svg>\n'
        '          <input id="q" type="search" placeholder="Search id, title, module, actions, reasons…" autocomplete="off" spellcheck="false" aria-label="Search cases">\n'
        '          <button class="x" id="qx" type="button" aria-label="Clear search" hidden>×</button>\n'
        "          <kbd>/</kbd>\n        </label>\n"
        '        <span class="tb-count"><b id="shown">'
        + total
        + "</b> of "
        + total
        + "</span>\n"
        + _toolbar_tail(opts, fgs)
    )


def _toolbar_tail(opts: str, fgs: str) -> str:
    """The toolbar's right half: filter button, sort, bulk buttons and the filter groups."""
    return (
        '        <span class="active-uc" id="active-uc" hidden><b></b><button type="button" aria-label="Clear use case">×</button></span>\n'
        '        <button type="button" class="chip" id="fbtn" aria-expanded="false" title="Show the filter groups">Filters<i id="fcount">0</i></button>\n'
        '        <label class="selectwrap">Sort\n          <select id="sort">'
        + opts
        + "</select>\n        </label>\n"
        '        <span class="bulk">\n          <button type="button" class="chip" id="expand-all">Expand shown</button>\n'
        '          <button type="button" class="chip" id="collapse-all">Collapse</button>\n        </span>\n      </div>\n'
        '      <div class="tb-row fgroups">\n        ' + fgs + "\n"
        '        <button type="button" class="chip ghost" id="clear" data-clear hidden>Clear filters</button>\n'
        "      </div>\n    </div>"
    )
