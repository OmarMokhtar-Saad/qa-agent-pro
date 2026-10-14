"""Geometry of the phone: zones, clickable containers, loose cards, the page."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import NamedTuple

from tools.mobile.sphn_rows import _cards, _rows
from tools.mobile.sphn_text import _desc, _inside, _rtl, _speaker, _text

#: Zone thresholds as fractions of the screen height.
TOP_BAR_BOTTOM = 0.12
COMPOSER_TOP = 0.86

#: A clickable container narrower than this fraction of the width is a chip.
CHIP_MAX_WIDTH = 0.42


class Page(NamedTuple):
    rtl: bool
    top_bar: list
    composer_zone: list
    blocks: list


class _Shape(NamedTuple):
    speaker: str
    ratio: float
    nlines: int


@dataclass
class _Claim:
    """The content of one page and the element ids its containers have claimed."""

    content: list
    rtl: bool
    width: int
    claimed: set = field(default_factory=set)

    def take(self, inner: list, container: dict) -> None:
        self.claimed.update(id(e) for _b, e in inner)
        self.claimed.add(id(container))


def _container_inner(item: tuple, claim: _Claim) -> list:
    cbox, container = item
    return [
        (b, e)
        for b, e in claim.content
        if id(e) != id(container) and id(e) not in claim.claimed and _inside(b, cbox)
    ]


def _container_speaker(labels: list, container: dict) -> str:
    speaker = ""
    for label in labels + [_desc(container)]:
        speaker = speaker or _speaker(label)
    return speaker


def _container_lines(texts: list, labels: list, rtl: bool) -> list:
    rows = _rows(texts, rtl) if texts else []
    return [r["text"] for r in rows] or [
        re.sub(r"^[^:]{1,24}:\s*", "", labels[0]) if labels else ""
    ]


def _block_kind(shape: _Shape) -> str:
    if shape.speaker:
        return "bubble"
    if shape.ratio < CHIP_MAX_WIDTH and shape.nlines == 1:
        return "chip"
    return "card"


def _container_block(item: tuple, inner: list, claim: _Claim) -> dict | None:
    cbox, container = item
    texts = [(b, e) for b, e in inner if _text(e)]
    labels = [_desc(e) for _b, e in inner if _desc(e) and not _text(e)]
    if not texts and not labels:
        return None
    claim.take(inner, container)
    speaker = _container_speaker(labels, container)
    lines = _container_lines(texts, labels, claim.rtl)
    shape = _Shape(speaker, (cbox[2] - cbox[0]) / float(claim.width), len(lines))
    return {
        "top": cbox[1],
        "bottom": cbox[3],
        "kind": _block_kind(shape),
        "speaker": speaker,
        "lines": lines,
    }


def _container_blocks(content: list, rtl: bool, width: int) -> tuple[list, set]:
    """Blocks for the clickable containers holding text, and the ids they claimed."""
    containers = [
        (b, e)
        for b, e in content
        if e.get("clickable") and not _text(e) and not e.get("editable")
    ]
    claim = _Claim(content, rtl, width)
    blocks: list = []
    for item in sorted(containers, key=lambda pair: pair[0][1]):
        block = _container_block(item, _container_inner(item, claim), claim)
        if block:
            blocks.append(block)
    return blocks, claim.claimed


def _split_zones(items: list, width: int, height: int) -> tuple[list, list, list]:
    """The top bar, the composer zone and the content between them."""
    top_bar = [(b, e) for b, e in items if b[3] <= height * TOP_BAR_BOTTOM]
    bottom = [(b, e) for b, e in items if b[1] >= height * COMPOSER_TOP]
    editable = [(b, e) for b, e in bottom if e.get("editable")]
    composer_zone = bottom if editable else []
    used = {id(e) for _b, e in top_bar} | {id(e) for _b, e in composer_zone}
    content = [(b, e) for b, e in items if id(e) not in used and (b[2] - b[0]) < width]
    return top_bar, composer_zone, content


def _loose_card_blocks(content: list, claimed: set, rtl: bool) -> list:
    """Free text outside any clickable container: rows -> cards."""
    loose = [(b, e) for b, e in content if id(e) not in claimed and _text(e)]
    return [
        {
            "top": card["top"],
            "bottom": card["bottom"],
            "kind": "card",
            "speaker": "",
            "lines": card["rows"],
        }
        for card in _cards(_rows(loose, rtl))
    ]


def page(items: list) -> Page:
    """Zone the elements and build the blocks, sorted top to bottom."""
    width = max(box[2] for box, _e in items) or 1
    height = max(box[3] for box, _e in items) or 1
    rtl = _rtl([_text(e) for _b, e in items if _text(e)])
    top_bar, composer_zone, content = _split_zones(items, width, height)
    blocks, claimed = _container_blocks(content, rtl, width)
    blocks.extend(_loose_card_blocks(content, claimed, rtl))
    blocks.sort(key=lambda block: block["top"])
    return Page(rtl, top_bar, composer_zone, blocks)
