"""Markup for the content blocks of the phone: bubbles, cards and chip strips."""

from __future__ import annotations

from typing import Callable

from tools.mobile.sphn_text import MAX_TEXT


def _chip_strip(chips: list, esc: Callable[..., str]) -> str:
    return (
        '<div class="ph-blocks live"><div>'
        + "".join(
            '<span class="ph-chip" dir="auto">' + esc(t, MAX_TEXT) + "</span>"
            for t in chips
        )
        + "</div></div>"
    )


def _block_markup(blocks: list, esc: Callable[..., str]) -> list:
    """The markup parts for the blocks, consecutive chips folded into one strip."""
    parts: list = []
    run_chips: list = []
    for block in blocks:
        if block["kind"] == "chip":
            run_chips.append(block["lines"][0])
            continue
        if run_chips:
            parts.append(_chip_strip(run_chips, esc))
            run_chips = []
        parts.append(_bubble_or_card_markup(block, esc))
    if run_chips:
        parts.append(_chip_strip(run_chips, esc))
    return parts


def _bubble_markup(block: dict, esc: Callable[..., str]) -> str:
    who = "user" if block["speaker"] == "user" else "sara"
    return (
        '<div class="ph-msg '
        + who
        + '"><div class="ph-bubble" dir="auto">'
        + esc(" ".join(block["lines"]), 600)
        + "</div></div>"
    )


def _one_line_card(line: str, esc: Callable[..., str]) -> str:
    return (
        '<div class="ph-blocks"><div class="ph-card"><div class="ph-row"><div class="ph-rowmain">'
        '<div class="ph-t" dir="auto">'
        + esc(line, MAX_TEXT)
        + "</div></div></div></div></div>"
    )


def _titled_card(lines: list, esc: Callable[..., str]) -> str:
    return (
        '<div class="ph-blocks"><div class="ph-card"><div class="ph-title" dir="auto">'
        + esc(lines[0], MAX_TEXT)
        + "</div>"
        + "".join(
            '<div class="ph-opt" dir="auto"><span>'
            + esc(line, MAX_TEXT)
            + "</span></div>"
            for line in lines[1:]
        )
        + "</div></div>"
    )


def _bubble_or_card_markup(block: dict, esc: Callable[..., str]) -> str:
    """One non-chip block: a chat bubble, a one-line card or a titled card."""
    if block["kind"] == "bubble":
        return _bubble_markup(block, esc)
    lines = block["lines"]
    if len(lines) == 1:
        return _one_line_card(lines[0], esc)
    return _titled_card(lines, esc)
