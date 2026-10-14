"""Markup for the phone chrome: the top bar and the composer."""

from __future__ import annotations

from typing import Callable

from tools.mobile.sphn_text import MAX_TEXT, _desc, _text

#: Composer-side controls that are a microphone rather than a send button.
MIC_WORDS = ("record", "voice", "mic", "speak", "dictate")


def _avatar(initial: dict | None, esc: Callable[..., str]) -> str:
    if initial is None:
        return ""
    return '<i class="ph-me">' + esc(_text(initial), 4) + "</i>"


def _brand(names: list, esc: Callable[..., str]) -> str:
    if not names:
        return ""
    return (
        '<span class="ph-brand" dir="auto">' + esc(" · ".join(names), 120) + "</span>"
    )


def _icons(icons: list, esc: Callable[..., str]) -> str:
    return "".join(
        '<i class="ph-ico'
        + (" kebab" if index == 0 else "")
        + '" title="'
        + esc(label, 80)
        + '">•</i>'
        for index, label in enumerate(icons[:4])
    )


def _top_bar_markup(top_bar: list, rtl: bool, esc: Callable[..., str]) -> str:
    """The status strip: the avatar initial, the names and up to four icons."""
    top_texts = [(b, e) for b, e in top_bar if _text(e)]
    top_texts.sort(key=lambda item: item[0][0], reverse=rtl)
    initial = next((e for _b, e in top_texts if len(_text(e)) == 1), None)
    names = [_text(e) for _b, e in top_texts if e is not initial]
    icons = [_desc(e) for _b, e in top_bar if _desc(e) and not _text(e)]
    return (
        '<div class="ph-top">'
        + _avatar(initial, esc)
        + _brand(names, esc)
        + _icons(icons, esc)
        + "</div>"
    )


def _placeholder(zone: list) -> str:
    for _b, e in zone:
        if _text(e) and not e.get("editable"):
            return _text(e)
    return ""


def _side_labels(zone: list) -> list:
    return [
        _desc(e)
        for _b, e in zone
        if _desc(e) and not _text(e) and not e.get("editable")
    ]


def _composer_markup(composer_zone: list, esc: Callable[..., str]) -> str:
    """The input bar, or ``""`` when the screen has no composer zone."""
    if not composer_zone:
        return ""
    side = _side_labels(composer_zone)
    mics = [s for s in side if any(w in s.lower() for w in MIC_WORDS)]
    others = [s for s in side if s not in mics]
    return (
        '<div class="ph-input">'
        + "".join('<i title="' + esc(s, 60) + '">↑</i>' for s in others[:3])
        + '<span dir="auto">'
        + esc(_placeholder(composer_zone) or " ", MAX_TEXT)
        + "</span>"
        + "".join('<i class="mic" title="' + esc(s, 60) + '">◉</i>' for s in mics[:3])
        + "</div>"
    )
