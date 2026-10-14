"""The shell's small vocabulary: pills, metrics, chips, check rows.

Mirrors the private helpers of ``tools/mobile/report.py`` by necessity; every
value goes through :func:`render_esc.ev_esc`.
"""

from __future__ import annotations

from typing import NamedTuple

from tools.mobile_evidence.render_esc import ev_esc, status_label


class Chip(NamedTuple):
    """One filter chip: ``count`` rows carry ``value`` in ``group``."""

    group: str
    value: str
    label: str
    n: int
    sw: str = ""


def pill(cls: str, label: object) -> str:
    return '<span class="pill ' + ev_esc(cls, 24) + '">' + ev_esc(label, 60) + "</span>"


def metric(value: object, label: str, cls: str = "") -> str:
    return (
        '<span class="m '
        + ev_esc(cls, 24)
        + '"><b>'
        + ev_esc(value, 40)
        + "</b><i>"
        + ev_esc(label, 40)
        + "</i></span>"
    )


def metric_pair(first: tuple, second: tuple, cls: str = "") -> str:
    """Two ``(value, label)`` pairs in one metric span."""
    return (
        '<span class="m '
        + ev_esc(cls, 24)
        + '"><b>'
        + ev_esc(first[0], 40)
        + "</b><i>"
        + ev_esc(first[1], 40)
        + "</i><b>"
        + ev_esc(second[0], 40)
        + "</b><i>"
        + ev_esc(second[1], 40)
        + "</i></span>"
    )


def chip(c: Chip) -> str:
    return (
        '<button type="button" class="chip" data-group="'
        + ev_esc(c.group, 24)
        + '" data-v="'
        + ev_esc(c.value, 60)
        + '" aria-pressed="false">'
        + (('<i class="sw ' + ev_esc(c.sw, 24) + '"></i>') if c.sw else "")
        + ev_esc(c.label, 60)
        + "<i>"
        + str(int(c.n))
        + "</i></button>"
    )


def empty(why: str) -> str:
    return '<p class="empty-note big">' + ev_esc(why, 400) + "</p>"


def check(pill_html: str, name: str, detail: str) -> str:
    """``pill_html`` is the already-built :func:`pill`."""
    return (
        "<li>"
        + pill_html
        + '<span class="ck" dir="auto">'
        + ev_esc(name, 120)
        + '</span><span class="cd" dir="auto">'
        + ev_esc(detail, 300)
        + "</span></li>"
    )


def status_pill(b: dict) -> str:
    ok = b.get("ok")
    return pill(
        "p-ok" if ok else ("p-void" if ok is None else "p-def"), status_label(b)
    )
