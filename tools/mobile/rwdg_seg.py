"""The outcome segment bar: tile order, segment text and the legend."""

from __future__ import annotations

from tools.mobile.rtxt_secret import esc
from tools.mobile.rvrd_tone import TILES, _tone


def _tile_order(counts: dict) -> list:
    """``(name, count)`` pairs: the known tiles first, then any other verdict."""
    ordered = [(name, int(counts.get(name) or 0)) for name in TILES]
    ordered += [
        (name, int(n)) for name, n in sorted(counts.items()) if name not in TILES
    ]
    return ordered


def _seg_text(n: int, label: str, total: int) -> str:
    """The count and word shown inside a segment, or nothing when it is too narrow."""
    # A number without its word is a colour-coded riddle: a segment too
    # narrow for both says nothing, and the legend below names every count.
    if n / total * 100 >= (len(label) + 3) * 1.6:
        return "<b>" + str(n) + "</b>" + esc(label.lower(), 40)
    return ""


def _seg_title(n: int, label: str) -> str:
    """A segment's tooltip text."""
    return str(n) + " " + esc(label.lower(), 40)


def _legend_open(name: str) -> str:
    """The start of one legend button, up to its jump value."""
    return (
        '<li><button type="button" data-jump-group="verdict" data-jump-value="'
        + esc(name, 24)
    )


def _legend_tail(n: int, label: str) -> str:
    """The count and word closing one legend button."""
    return "<b>" + str(n) + "</b>" + esc(label, 40) + "</button></li>"


def _segbar(counts: dict) -> str:
    total = sum(int(n) for n in counts.values()) or 1
    rows = [(name, n, _tone(name)) for name, n in _tile_order(counts) if n]
    segs = [
        f'<span class="{t[1]}" style="flex:{n}" title="{_seg_title(n, t[3])}">{_seg_text(n, t[3], total)}</span>'
        for _name, n, t in rows
    ]
    legend = [
        _legend_open(name) + f'"><i class="sw {t[0]}"></i>' + _legend_tail(n, t[3])
        for name, n, t in rows
    ]
    return (
        '<figure class="seg"><figcaption><b>Outcomes</b><span>what each case came to</span></figcaption>'
        # The legend is the readable copy; the bar is its picture.
        '<div class="segbar" aria-hidden="true">'
        + "".join(segs)
        + '</div><ul class="seglegend">'
        + "".join(legend)
        + "</ul></figure>"
    )
