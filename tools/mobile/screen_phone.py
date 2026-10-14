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

from typing import Callable

from tools.mobile.sphn_blocks import _block_markup
from tools.mobile.sphn_chrome import _composer_markup, _top_bar_markup
from tools.mobile.sphn_layout import page
from tools.mobile.sphn_text import _elements

_EMPTY_PHONE = (
    '<div class="phone" dir="auto"><div class="ph-scroll">'
    '<div class="ph-empty">no element on this screen carried text</div>'
    "</div></div>"
)


def _phone(rtl: bool, pieces: tuple) -> str:
    top, body, composer = pieces
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


def compose(screen: object, *, esc: Callable[..., str], app: str = "") -> str:
    """The phone for one pruned screen, or an honest empty phone."""
    items = _elements(screen)
    if not items:
        return _EMPTY_PHONE
    laid = page(items)
    parts = _block_markup(laid.blocks, esc)
    top = _top_bar_markup(laid.top_bar, laid.rtl, esc)
    composer = _composer_markup(laid.composer_zone, esc)
    body = (
        "".join(parts)
        or '<div class="ph-empty">nothing but chrome on this screen</div>'
    )
    return _phone(laid.rtl, (top, body, composer))
