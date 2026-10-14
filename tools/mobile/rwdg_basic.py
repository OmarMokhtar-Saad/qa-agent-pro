"""Small HTML widgets: pills, metrics, KPI tiles, filter chips and section heads."""

from __future__ import annotations

from tools.mobile.rtxt_secret import esc
from tools.mobile.rwdg_args import _Chip, _Head, _Kpi, _SecBlock

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


def _kpi(k: _Kpi) -> str:
    """One KPI tile from its parameter object."""
    return (
        '<div class="kpi'
        + (" hero" if k.hero else "")
        + '"><div class="kv'
        + ((" " + esc(k.tone, 24)) if k.tone else "")
        + '">'
        + k.value
        + '</div><div class="kl">'
        + esc(k.label, 80)
        + "</div>"
        + (('<div class="kd">' + k.detail + "</div>") if k.detail else "")
        + "</div>"
    )


def _chip(c: _Chip) -> str:
    """One filter chip from its parameter object."""
    return (
        '<button type="button" class="chip" data-group="'
        + esc(c.group, 24)
        + '" data-v="'
        + esc(c.value, 60)
        + '" aria-pressed="false">'
        + (('<i class="sw ' + esc(c.sw, 24) + '"></i>') if c.sw else "")
        + esc(c.label, 60)
        + "<i>"
        + str(int(c.num))
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


def _sec_block(b: _SecBlock) -> str:
    """One collapsible section from its parameter object."""
    return (
        '<div class="apis"><details class="sec"'
        + (" open" if b.open_ else "")
        + '><summary><span class="chev" aria-hidden="true"></span>'
        + esc(b.title, 80)
        + (
            ('<span class="cnt">' + str(int(b.num)) + "</span>")
            if b.num not in (None, "")
            else ""
        )
        + (('<span class="mt">' + esc(b.note, 160) + "</span>") if b.note else "")
        + '</summary><div class="secbody">'
        + b.body
        + "</div></details></div>"
    )


# ``head.lede`` is markup built through esc; the ids are constants.
# Level 3 is for a section only ever drawn inside a Diagnostics part, which
# <h2>Diagnostics</h2> already heads: an h2 there reads, in a screen reader's
# outline, as a sibling of Diagnostics rather than a part of it.
def _sechead(sid: str, head: _Head, body: str) -> str:
    """One page section: its id, head and body markup."""
    tag = "h3" if head.level == 3 else "h2"
    return (
        '\n  <section id="'
        + sid
        + '">\n    <div class="sechead"><'
        + tag
        + ">"
        + esc(head.title, 80)
        + "</"
        + tag
        + '><span class="label">'
        + esc(head.label, 80)
        + "</span></div>\n"
        + (('    <p class="lede">' + head.lede + "</p>\n") if head.lede else "")
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
