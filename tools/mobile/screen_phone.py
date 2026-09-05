"""Draw a pruned screen the way the app draws it: chrome, bubbles, cards, chips, composer.

The report used to show every screen as a wireframe of element bounds -- honest, and
unreadable. The reference device-run report draws the screen as the user saw it. This
module composes that picture from the SAME pruned ``uiautomator`` dump the wireframe
used, so nothing here is a screenshot and nothing is invented: every word on the phone
is an element's ``text`` or ``content-desc``, placed by that element's bounds.

**How the dump becomes a phone.** Geometry decides the zone: elements whose bottom sits
in the top eighth of the screen are the app bar; the editable field and the clickable
controls beside it at the bottom are the composer; everything else is content. In the
content zone a CLICKABLE container that holds text is one block -- a bubble when its
accessibility label names a speaker ("You said:", "Assistant replied:"), a chip when it
is narrow, an option card otherwise. Text that sits in no clickable container is grouped
into rows by vertical overlap and into cards by proximity, and read in the screen's own
direction (right-to-left when the words are mostly Arabic).

**What this cannot know.** Fonts, colours, icons and images are not in a dump, so a
tile drawn as a bold number in the app is a line of text here, and an icon is a dot with
its label as a tooltip. The element map (the wireframe) stays available under the phone
for anyone who needs the exact rectangles.

**Every string here came from the app on the device.** The one exit to markup is the
``esc`` callable the caller hands in -- ``report.esc``, which neutralises guard markers
and HTML-escapes with ``quote=True``. No f-string interpolates a store value.
"""

from __future__ import annotations

import re
from typing import Callable

#: Zone thresholds as fractions of the screen height.
TOP_BAR_BOTTOM = 0.12
COMPOSER_TOP = 0.86

#: A clickable container narrower than this fraction of the width is a chip.
CHIP_MAX_WIDTH = 0.42

#: Rows closer than this many row-heights belong to one card.
CARD_GAP_ROWS = 1.6

#: Speaker labels the composer recognises on a container's accessibility label. Generic
#: English forms; a profile may not add to them (no vendor word lives in tools/mobile).
USER_PREFIXES = ("you said:", "you:", "user:", "you typed:")
ASSISTANT_PREFIXES = ("assistant replied:", "assistant:", "replied:", "bot:", "reply:")

#: Composer-side controls that are a microphone rather than a send button.
MIC_WORDS = ("record", "voice", "mic", "speak", "dictate")

# The Arabic block, written as escapes: the reference-leak gate forbids any RTL
# codepoint in this tree's source, and the regex engine reads both forms alike.
_ARABIC = re.compile("[\u0600-\u06ff]")

MAX_ROWS = 120
MAX_TEXT = 220


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _box(element: dict) -> tuple | None:
    raw = element.get("bounds")
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    values = [_int(v) for v in raw]
    if any(v is None for v in values):
        return None
    left, right = sorted((values[0], values[2]))
    top, bottom = sorted((values[1], values[3]))
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def _inside(inner: tuple, outer: tuple) -> bool:
    return (
        inner[0] >= outer[0] - 2
        and inner[1] >= outer[1] - 2
        and inner[2] <= outer[2] + 2
        and inner[3] <= outer[3] + 2
    )


def _text(element: dict) -> str:
    return " ".join(str(element.get("text") or "").split())[:MAX_TEXT]


def _desc(element: dict) -> str:
    return " ".join(str(element.get("desc") or "").split())[:MAX_TEXT]


def _rtl(texts: list) -> bool:
    joined = " ".join(texts)
    letters = sum(1 for ch in joined if ch.isalpha())
    return letters > 0 and len(_ARABIC.findall(joined)) * 2 > letters


def _speaker(label: str) -> str:
    low = label.lower()
    if any(low.startswith(p) for p in USER_PREFIXES):
        return "user"
    if any(low.startswith(p) for p in ASSISTANT_PREFIXES):
        return "assistant"
    return ""


def _elements(screen: object) -> list:
    body = screen if isinstance(screen, dict) else {}
    out = []
    for element in body.get("elements") or []:
        if not isinstance(element, dict):
            continue
        box = _box(element)
        if box is None:
            continue
        out.append((box, element))
    return out


def _rows(leaves: list, rtl: bool) -> list:
    """Group text leaves into rows by vertical overlap; each row is one line."""
    ordered = sorted(leaves, key=lambda item: (item[0][1], item[0][0]))
    rows: list = []
    for box, element in ordered:
        if rows:
            last = rows[-1]
            top, bottom = last["top"], last["bottom"]
            overlap = min(bottom, box[3]) - max(top, box[1])
            if overlap > 0 and overlap * 2 >= min(bottom - top, box[3] - box[1]):
                last["items"].append((box, element))
                last["top"] = min(top, box[1])
                last["bottom"] = max(bottom, box[3])
                last["left"] = min(last["left"], box[0])
                last["right"] = max(last["right"], box[2])
                continue
        rows.append(
            {
                "items": [(box, element)],
                "top": box[1],
                "bottom": box[3],
                "left": box[0],
                "right": box[2],
            }
        )
    for row in rows:
        items = sorted(row["items"], key=lambda item: item[0][0], reverse=rtl)
        row["text"] = " ".join(_text(e) for _b, e in items if _text(e))
    return [row for row in rows if row["text"]][:MAX_ROWS]


def _cards(rows: list) -> list:
    """Rows close together are one card; the first row of a multi-row card is its title."""
    cards: list = []
    for row in rows:
        height = max(1, row["bottom"] - row["top"])
        if cards and row["top"] - cards[-1]["bottom"] < CARD_GAP_ROWS * height:
            cards[-1]["rows"].append(row["text"])
            cards[-1]["bottom"] = row["bottom"]
            continue
        cards.append(
            {"top": row["top"], "bottom": row["bottom"], "rows": [row["text"]]}
        )
    return cards


def compose(screen: object, *, esc: Callable[..., str], app: str = "") -> str:
    """The phone for one pruned screen, or an honest empty phone."""
    items = _elements(screen)
    if not items:
        return (
            '<div class="phone" dir="auto"><div class="ph-scroll">'
            '<div class="ph-empty">no element on this screen carried text</div>'
            "</div></div>"
        )
    width = max(box[2] for box, _e in items) or 1
    height = max(box[3] for box, _e in items) or 1
    rtl = _rtl([_text(e) for _b, e in items if _text(e)])

    top_bar = [(b, e) for b, e in items if b[3] <= height * TOP_BAR_BOTTOM]
    bottom = [(b, e) for b, e in items if b[1] >= height * COMPOSER_TOP]
    editable = [(b, e) for b, e in bottom if e.get("editable")]
    composer_zone = bottom if editable else []
    used = {id(e) for _b, e in top_bar} | {id(e) for _b, e in composer_zone}
    content = [(b, e) for b, e in items if id(e) not in used and (b[2] - b[0]) < width]

    # Clickable containers holding text are blocks of their own.
    containers = [
        (b, e)
        for b, e in content
        if e.get("clickable") and not _text(e) and not e.get("editable")
    ]
    blocks: list = []
    claimed: set = set()
    for cbox, container in sorted(containers, key=lambda item: item[0][1]):
        inner = [
            (b, e)
            for b, e in content
            if id(e) != id(container) and id(e) not in claimed and _inside(b, cbox)
        ]
        texts = [(b, e) for b, e in inner if _text(e)]
        labels = [_desc(e) for _b, e in inner if _desc(e) and not _text(e)]
        if not texts and not labels:
            continue
        for _b, e in inner:
            claimed.add(id(e))
        claimed.add(id(container))
        speaker = ""
        for label in labels + [_desc(container)]:
            speaker = speaker or _speaker(label)
        rows = _rows(texts, rtl) if texts else []
        lines = [r["text"] for r in rows] or [
            re.sub(r"^[^:]{1,24}:\s*", "", labels[0]) if labels else ""
        ]
        ratio = (cbox[2] - cbox[0]) / float(width)
        if speaker:
            kind = "bubble"
        elif ratio < CHIP_MAX_WIDTH and len(lines) == 1:
            kind = "chip"
        else:
            kind = "card"
        blocks.append(
            {
                "top": cbox[1],
                "bottom": cbox[3],
                "kind": kind,
                "speaker": speaker,
                "lines": lines,
            }
        )

    # Free text outside any clickable container: rows -> cards.
    loose = [(b, e) for b, e in content if id(e) not in claimed and _text(e)]
    for card in _cards(_rows(loose, rtl)):
        blocks.append(
            {
                "top": card["top"],
                "bottom": card["bottom"],
                "kind": "card",
                "speaker": "",
                "lines": card["rows"],
            }
        )
    blocks.sort(key=lambda block: block["top"])

    # ---- markup --------------------------------------------------------------
    parts = []
    run_chips: list = []

    def flush_chips() -> None:
        if run_chips:
            parts.append(
                '<div class="ph-blocks live"><div>'
                + "".join(
                    '<span class="ph-chip" dir="auto">' + esc(t, MAX_TEXT) + "</span>"
                    for t in run_chips
                )
                + "</div></div>"
            )
            run_chips.clear()

    for block in blocks:
        if block["kind"] == "chip":
            run_chips.append(block["lines"][0])
            continue
        flush_chips()
        if block["kind"] == "bubble":
            who = "user" if block["speaker"] == "user" else "sara"
            parts.append(
                '<div class="ph-msg '
                + who
                + '"><div class="ph-bubble" dir="auto">'
                + esc(" ".join(block["lines"]), 600)
                + "</div></div>"
            )
            continue
        lines = block["lines"]
        if len(lines) == 1:
            parts.append(
                '<div class="ph-blocks"><div class="ph-card"><div class="ph-row"><div class="ph-rowmain">'
                '<div class="ph-t" dir="auto">'
                + esc(lines[0], MAX_TEXT)
                + "</div></div></div></div></div>"
            )
        else:
            parts.append(
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
    flush_chips()

    # ---- chrome --------------------------------------------------------------
    top_texts = [(b, e) for b, e in top_bar if _text(e)]
    top_texts.sort(key=lambda item: item[0][0], reverse=rtl)
    initial = next((e for _b, e in top_texts if len(_text(e)) == 1), None)
    names = [_text(e) for _b, e in top_texts if e is not initial]
    icons = [_desc(e) for _b, e in top_bar if _desc(e) and not _text(e)]
    top = (
        '<div class="ph-top">'
        + (
            ('<i class="ph-me">' + esc(_text(initial), 4) + "</i>")
            if initial is not None
            else ""
        )
        + (
            '<span class="ph-brand" dir="auto">'
            + esc(" · ".join(names), 120)
            + "</span>"
            if names
            else ""
        )
        + "".join(
            '<i class="ph-ico'
            + (" kebab" if index == 0 else "")
            + '" title="'
            + esc(label, 80)
            + '">•</i>'
            for index, label in enumerate(icons[:4])
        )
        + "</div>"
    )
    if composer_zone:
        placeholder = ""
        for _b, e in composer_zone:
            if _text(e) and not e.get("editable"):
                placeholder = _text(e)
                break
        side = [
            _desc(e)
            for _b, e in composer_zone
            if _desc(e) and not _text(e) and not e.get("editable")
        ]
        mics = [s for s in side if any(w in s.lower() for w in MIC_WORDS)]
        others = [s for s in side if s not in mics]
        composer = (
            '<div class="ph-input">'
            + "".join('<i title="' + esc(s, 60) + '">↑</i>' for s in others[:3])
            + '<span dir="auto">'
            + esc(placeholder or " ", MAX_TEXT)
            + "</span>"
            + "".join(
                '<i class="mic" title="' + esc(s, 60) + '">◉</i>' for s in mics[:3]
            )
            + "</div>"
        )
    else:
        composer = ""
    body = (
        "".join(parts)
        or '<div class="ph-empty">nothing but chrome on this screen</div>'
    )
    return (
        '<div class="phone" dir="'
        + ("rtl" if rtl else "ltr")
        + '">'
        + top
        + '<div class="ph-scroll">'
        + body
        + "</div>"
        + composer
        + "</div>"
    )
