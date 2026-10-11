"""Small HTML widgets shared by the report sections: pills, KPI tiles, section heads and the run wrapper."""

from __future__ import annotations

from dataclasses import dataclass

from tools.mobile.report_text import esc
from tools.mobile.report_verdict import TILES, _tone

RAIL = {"pass": "ok", "fail": "def", "blocked": "gap", "unverified": "gap"}


def _pill(cls: str, label: object) -> str:
    return '<span class="pill ' + esc(cls, 24) + '">' + esc(label, 60) + "</span>"


def _metric(value: object, label: str, cls: str = "") -> str:
    return (
        '<span class="m '
        + esc(cls, 24)
        + '"><b>'
        + esc(value, 40)
        + "</b><i>"
        + esc(label, 40)
        + "</i></span>"
    )


MSEP = '<span class="msep"></span>'


def _kpi(
    value: str, label: str, detail: str = "", tone: str = "", hero: bool = False
) -> str:
    """``value`` and ``detail`` arrive as markup already built through :func:`esc`."""
    return (
        '<div class="kpi'
        + (" hero" if hero else "")
        + '"><div class="kv'
        + ((" " + esc(tone, 24)) if tone else "")
        + '">'
        + value
        + '</div><div class="kl">'
        + esc(label, 80)
        + "</div>"
        + (('<div class="kd">' + detail + "</div>") if detail else "")
        + "</div>"
    )


def _chip(group: str, value: str, label: str, count: int, sw: str = "") -> str:
    return (
        '<button type="button" class="chip" data-group="'
        + esc(group, 24)
        + '" data-v="'
        + esc(value, 60)
        + '" aria-pressed="false">'
        + (('<i class="sw ' + esc(sw, 24) + '"></i>') if sw else "")
        + esc(label, 60)
        + "<i>"
        + str(int(count))
        + "</i></button>"
    )


def _all_chip(group: str, count: int) -> str:
    return (
        '<button type="button" class="chip on" data-group="'
        + esc(group, 24)
        + '" data-v="*" aria-pressed="true">All<i>'
        + str(int(count))
        + "</i></button>"
    )


def _filter_group(label: str, chips: str) -> str:
    if not chips:
        return ""
    return (
        '<div class="fg"><span class="fgl">'
        + esc(label, 40)
        + "</span>"
        + chips
        + "</div>"
    )


def _sec_block(
    title: str, body: str, count: object = None, note: str = "", open_: bool = False
) -> str:
    return (
        '<div class="apis"><details class="sec"'
        + (" open" if open_ else "")
        + '><summary><span class="chev" aria-hidden="true"></span>'
        + esc(title, 80)
        + (
            ('<span class="cnt">' + str(int(count)) + "</span>")
            if count not in (None, "")
            else ""
        )
        + (('<span class="mt">' + esc(note, 160) + "</span>") if note else "")
        + '</summary><div class="secbody">'
        + body
        + "</div></details></div>"
    )


@dataclass(frozen=True)
class _PageData:
    """What the evidence builders read together, so each takes one argument.

    Every field is optional: a builder that needs only some of them (the facts
    strip wants ``loaded`` and ``coverage``) passes the rest at their defaults.
    """

    library: object = None
    app: str = ""
    loaded: dict | None = None
    coverage: dict | None = None
    media_map: object = None


# Six parameters, kept: tests/mobile/test_mobile_report_sections.py calls this
# with five positionals plus ``level=``, so the signature is pinned from outside.
def _sechead(  # noqa: PLR0913
    sid: str, title: str, label: str, lede: str, body: str, level: int = 2
) -> str:
    """``lede`` is markup built through :func:`esc`; the ids are constants.

    ``level`` 3 is for a section only ever drawn inside a Diagnostics part, which
    ``<h2>Diagnostics</h2>`` already heads: an h2 there reads, in a screen reader's
    outline, as a sibling of Diagnostics rather than a part of it.
    """
    tag = "h3" if level == 3 else "h2"
    return (
        '\n  <section id="'
        + sid
        + '">\n    <div class="sechead"><'
        + tag
        + ">"
        + esc(title, 80)
        + "</"
        + tag
        + '><span class="label">'
        + esc(label, 80)
        + "</span></div>\n"
        + (('    <p class="lede">' + lede + "</p>\n") if lede else "")
        + body
        + "\n  </section>\n"
    )


def _outcome_pill(outcome: str) -> str:
    low = outcome.lower()
    if low in ("ok", "done", "pass") or low.endswith("_pass") or low.endswith("_ok"):
        cls = "p-ok"
    elif low in ("-", "", "skipped", "pending"):
        cls = "p-void"
    elif "fail" in low or "error" in low or "blocked" in low or "refus" in low:
        cls = "p-def"
    elif "escape" in low or "needs" in low or "ask" in low or "wait" in low:
        cls = "p-gap"
    else:
        cls = "p-void"
    return _pill(cls, outcome)


def _tile_order(counts: dict) -> list:
    """``(name, count)`` pairs: the known tiles first, then any other verdict."""
    ordered = [(name, int(counts.get(name) or 0)) for name in TILES]
    ordered += [
        (name, int(n)) for name, n in sorted(counts.items()) if name not in TILES
    ]
    return ordered


def _segbar(counts: dict) -> str:
    total = sum(int(n) for n in counts.values()) or 1
    segs, legend = [], []
    for name, n in _tile_order(counts):
        if not n:
            continue
        sw, seg, _pill_cls, label = _tone(name)
        pct = n / total * 100
        # A number without its word is a colour-coded riddle: a segment too
        # narrow for both says nothing, and the legend below names every count.
        if pct >= (len(label) + 3) * 1.6:
            text = "<b>" + str(n) + "</b>" + esc(label.lower(), 40)
        else:
            text = ""
        segs.append(
            '<span class="'
            + seg
            + '" style="flex:'
            + str(n)
            + '" title="'
            + str(n)
            + " "
            + esc(label.lower(), 40)
            + '">'
            + text
            + "</span>"
        )
        legend.append(
            '<li><button type="button" data-jump-group="verdict" data-jump-value="'
            + esc(name, 24)
            + '"><i class="sw '
            + sw
            + '"></i><b>'
            + str(n)
            + "</b>"
            + esc(label, 40)
            + "</button></li>"
        )
    return (
        '<figure class="seg"><figcaption><b>Outcomes</b><span>what each case came to</span></figcaption>'
        # The legend is the readable copy; the bar is its picture.
        '<div class="segbar" aria-hidden="true">'
        + "".join(segs)
        + '</div><ul class="seglegend">'
        + "".join(legend)
        + "</ul></figure>"
    )


@dataclass(frozen=True)
class _Run:
    """What every section of one page is built from, read ONCE."""

    run_id: str
    manifest: dict
    cases: list
    facts: list
    library: dict
    loaded: dict | None
    coverage: dict
