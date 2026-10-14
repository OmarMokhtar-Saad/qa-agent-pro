"""Group text leaves into rows and rows into cards."""

from __future__ import annotations

from tools.mobile.sphn_text import _text

MAX_ROWS = 120

#: Rows closer than this many row-heights belong to one card.
CARD_GAP_ROWS = 1.6


def _joins(last: dict, box: tuple) -> bool:
    overlap = min(last["bottom"], box[3]) - max(last["top"], box[1])
    return overlap > 0 and overlap * 2 >= min(
        last["bottom"] - last["top"], box[3] - box[1]
    )


def _grow(last: dict, item: tuple) -> None:
    box = item[0]
    last["items"].append(item)
    last["top"] = min(last["top"], box[1])
    last["bottom"] = max(last["bottom"], box[3])
    last["left"] = min(last["left"], box[0])
    last["right"] = max(last["right"], box[2])


def _new_row(item: tuple) -> dict:
    box = item[0]
    return {
        "items": [item],
        "top": box[1],
        "bottom": box[3],
        "left": box[0],
        "right": box[2],
    }


def _finish_row(row: dict, rtl: bool) -> None:
    items = sorted(row["items"], key=lambda item: item[0][0], reverse=rtl)
    row["text"] = " ".join(_text(e) for _b, e in items if _text(e))


def _rows(leaves: list, rtl: bool) -> list:
    """Group text leaves into rows by vertical overlap; each row is one line."""
    rows: list = []
    for item in sorted(leaves, key=lambda item: (item[0][1], item[0][0])):
        if rows and _joins(rows[-1], item[0]):
            _grow(rows[-1], item)
        else:
            rows.append(_new_row(item))
    for row in rows:
        _finish_row(row, rtl)
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
