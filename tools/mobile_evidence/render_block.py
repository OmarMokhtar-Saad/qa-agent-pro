"""Tile and section blocks: the KPI tile and the collapsible section."""

from __future__ import annotations

from typing import NamedTuple

from tools.mobile_evidence.render_esc import ev_esc


class Kpi(NamedTuple):
    """``value`` and ``detail`` arrive as markup already built through ``ev_esc``."""

    value: str
    label: str
    detail: str = ""
    key: str = ""


class SecHead(NamedTuple):
    """The optional count, note and open state of a section's summary line."""

    total: int | str | None = None
    note: str = ""
    open_: bool = False


def kpi(k: Kpi) -> str:
    return (
        '<div class="kpi"'
        + ((' data-series="' + ev_esc(k.key, 24) + '"') if k.key else "")
        + '><div class="kv">'
        + k.value
        + '</div><div class="kl">'
        + ev_esc(k.label, 80)
        + "</div>"
        + (('<div class="kd">' + k.detail + "</div>") if k.detail else "")
        + "</div>"
    )


def gap_kpi(label: str, why: str, key: str = "") -> str:
    return kpi(
        Kpi(
            '<span class="kv-gap">n/a</span>',
            label,
            '<span class="kd-gap">' + ev_esc(why, 400) + "</span>",
            key,
        )
    )


def sec_block(title: str, body: str, head: SecHead = SecHead()) -> str:
    return (
        '<div class="apis"><details class="sec"'
        + (" open" if head.open_ else "")
        + '><summary><span class="chev" aria-hidden="true"></span>'
        + ev_esc(title, 80)
        + _count(head.total)
        + (
            ('<span class="mt">' + ev_esc(head.note, 160) + "</span>")
            if head.note
            else ""
        )
        + '</summary><div class="secbody">'
        + body
        + "</div></details></div>"
    )


def _count(count: int | str | None) -> str:
    if count in (None, ""):
        return ""
    return '<span class="cnt">' + str(int(count)) + "</span>"
