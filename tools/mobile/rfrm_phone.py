"""The phone drawn for one screen, the element-map fold under it and the sequence frames."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tools.mobile import screen_phone
from tools.mobile.report_text import esc
from tools.mobile.report_widgets import _PageData
from tools.mobile.rfrm_frame import _frame_html
from tools.mobile.rfrm_look import (
    AMBIGUOUS_LOOK,
    LOOK_AMBIGUOUS,
    LOOK_EXACT,
    NOT_CAPTURED,
    _resolve_look,
)
from tools.mobile.rfrm_shot import _shot_html

_WIRE_LEGEND = (
    '<ul class="wirelegend"><li><i class="sw tap"></i>tappable</li>'
    '<li><i class="sw edit"></i>editable</li><li><i class="sw"></i>other element</li></ul>'
)


def _element_map_fold(screen_id: object, screens: object) -> str:
    """The folded element-rectangle wireframe shown under the phone."""
    return (
        '<details class="sec"><summary><span class="chev" aria-hidden="true"></span>'
        'Element map<span class="mt">the same screen as its element rectangles</span></summary>'
        '<div class="secbody"><div class="phone wire"><div class="ph-scroll">'
        + _frame_html(screen_id, screens)
        + "</div></div>"
        + _WIRE_LEGEND
        + "</div></details>"
    )


def _empty_phone(verdict: str) -> str:
    """An empty handset that says why no look is drawn."""
    return (
        '<div class="phone" dir="auto"><div class="ph-scroll"><div class="ph-empty">'
        + esc(AMBIGUOUS_LOOK if verdict == LOOK_AMBIGUOUS else NOT_CAPTURED)
        + "</div></div></div>"
    )


@dataclass(frozen=True)
class _PhoneHead:
    """What heads one phone: its title, its sub-line and the screen key it shows."""

    title: str
    sub: str
    screen_id: object


# The photograph first, the composition under it: the drawing is an
# annotation of the screen, not a substitute for it, and a reader who
# sees the real thing first can tell which is which.
#
# INSIDE this branch and nowhere else. `_resolve_look` has already said
# this key names ONE stored look; on its `LOOK_AMBIGUOUS` answer the
# else-branch below draws an empty handset and says the look is
# unknowable, and a photograph hung above that sentence would contradict
# it with the one kind of evidence a tester cannot argue with.
# THE PICTURE GETS THE SAME ANSWER AS THE FOLD BENEATH IT. Until this
# change the fold said the look was unknowable while the phone above it
# drew the newest one: one page contradicting itself, because this
# function resolved the library itself and the round-two guard governed
# only `_frame_html`, which is the fold. `ph-empty` is an existing rule
# in ``report_shell.html`` -- no new class name is introduced, because a
# class with no rule renders unstyled with nothing failing.
def _phone_layers(look: _PhoneHead, page: _PageData, resolved: tuple) -> tuple:
    """The handset markup and the element-map fold for one resolved look."""
    ident, screen, verdict = resolved
    if screen is not None:
        drawn = _shot_html(ident) + screen_phone.compose(screen, esc=esc, app=page.app)
        return drawn, _element_map_fold(look.screen_id, page.library)
    return _empty_phone(verdict), ""


# Both pictures come from the same pruned dump. ``screen_phone.compose`` reads
# the elements' text, labels and bounds into the app bar, bubbles, cards, chips
# and composer the user saw; the wireframe keeps the exact rectangles for
# anyone who needs them. Above both sits the PNG the lane captured at that
# observation, when one was stored and this page could afford it; where it
# could not, the note in its place says which of those two things happened.
def _phone_html(look: _PhoneHead, page: _PageData) -> str:
    """The screen as the app drew it, with the element map folded beneath it."""
    ident, screen, verdict = _resolve_look(look.screen_id, page.library)
    drawn, fold = _phone_layers(look, page, (ident, screen, verdict))
    return (
        '<div class="phone-col" data-look="'
        + esc(verdict, 16)
        + '" data-key="'
        + esc(ident, 40)
        + '"><h4>'
        + esc(look.title, 40)
        + '<span class="mt">'
        + esc(look.sub, 80)
        + "</span></h4>"
        + drawn
        + fold
        + "</div>"
    )


# The Run sequence has TWO renderers and only one of them is ``_seq_rows``.
# When a case has app evidence, ``_card_html`` renders
# ``ev_render.sequence_items(...)`` INSTEAD -- that stream REPLACES the seq
# rows whole -- so a picture threaded only into ``_seq_rows`` renders on no
# such run at all, while this page's own prose promises one for every step.
# Two render sites closed out of three is a promise that is false exactly
# where nobody looked.
#
# The indices are the ones ``sequence_items`` already aligns its trace against
# (``rows[i]``), so this is the SAME alignment rather than a second one.
#
# Built by :func:`_shot_html`, THE producer of this markup, and only on
# :func:`_resolve_look`'s ``LOOK_EXACT`` answer -- the same gate
# :func:`_phone_html` honours, so the merged stream cannot show one arbitrary
# look of an ambiguous screen as the step's own.
def _seq_frames(rows: Any, screens: object) -> dict:
    """``{trace index: frame html}`` for the merged app-evidence stream."""
    out: dict = {}
    for index, row in enumerate(list(rows or [])):
        if not isinstance(row, dict):
            continue
        ident, screen, verdict = _resolve_look(row.get("after"), screens)
        if screen is None or verdict != LOOK_EXACT:
            continue
        html = _shot_html(ident)
        if html:
            out[index] = html
    return out
