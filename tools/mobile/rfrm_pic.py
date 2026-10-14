"""Step pictures: the device's screenshot or the drawing, with the tap overlay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tools.mobile import report_images, run_store, screen_phone
from tools.mobile.report_media import _IMAGES, _img_html, _ImgSpec
from tools.mobile.report_text import esc
from tools.mobile.report_widgets import _PageData
from tools.mobile.rfrm_extent import _bounds_of, _device_size
from tools.mobile.rfrm_look import (
    AMBIGUOUS_LOOK,
    LOOK_AMBIGUOUS,
    LOOK_EXACT,
    NOT_CAPTURED,
    _resolve_look,
)
from tools.mobile.rfrm_shot import _SHOTS

#: ``sizes`` for a step picture and for a filmstrip thumbnail.
STEP_SIZES = "(max-width: 600px) 44vw, 240px"


#: The ops whose target is an element on the screen before the step.
TARGET_OPS = ("tap", "long_press", "type", "clear")


def _tap_overlay(target_id: object, screen: object) -> str:
    """The acted-on element's box and a tap dot, in CSS percentages, or ``""``."""
    tid = str(target_id or "")
    body = screen if isinstance(screen, dict) else {}
    if not tid:
        return ""
    for element in list(body.get("elements") or []):
        if isinstance(element, dict) and str(element.get("id") or "") == tid:
            dev_w, dev_h = _device_size(body)
            box = report_images.overlay_style(_bounds_of(element), dev_w, dev_h)
            if not box:
                return ""
            return (
                '<span class="tapbox" aria-hidden="true" style="left:{left}%;top:{top}%;'
                'width:{width}%;height:{height}%"></span><span class="tapdot" '
                'aria-hidden="true" style="left:{dot_x}%;top:{dot_y}%"></span>'
            ).format(**{k: float(v) for k, v in box.items()})
    return ""


@dataclass(frozen=True)
class _Caption:
    """What one step picture is called: its caption, the element acted on, a note."""

    text: str
    target_id: str = ""
    note: str = ""


def _shot_spec(filename: str, book: dict, text: str) -> _ImgSpec:
    """The image spec of one stored step picture."""
    return _ImgSpec(
        "../" + run_store.SHOTS_DIR,
        filename,
        book["shots_dir"] / filename,
        text + ": the screen as the device drew it",
        STEP_SIZES,
    )


# The note stands IN PLACE of a picture, never beside one. The drawing is
# composed only when no picture was stored for an exact look.
#
# Every figure is stamped with what `_resolve_look` returned: ``data-look``
# the verdict and ``data-screen`` the key it resolved. The page-level sweep in
# ``test_mobile_screen_observations`` reads those, so a caller that resolved
# the library itself would be graded on the day it was written.
def _look_pic(key: object, page: _PageData, cap: _Caption) -> str:
    """One moment of a step: the device's picture, the drawing, or why neither."""
    ident, screen, verdict = _resolve_look(key, page.library)
    shot = (' data-screen="' + esc(ident, 40) + '"') if ident else ""
    head = '<figure class="pic" data-look="' + esc(verdict, 16) + '"' + shot + ">"
    label = esc(cap.text, 40) + (
        ('<span class="picsub">' + esc(cap.note, 80) + "</span>") if cap.note else ""
    )
    if screen is not None and verdict == LOOK_EXACT:
        return head + _exact_pic_body((ident, screen, label), page, cap)
    gap = AMBIGUOUS_LOOK if verdict == LOOK_AMBIGUOUS else NOT_CAPTURED
    return (
        head
        + '<p class="picnote">'
        + esc(gap, 300)
        + "</p><figcaption>"
        + label
        + "</figcaption></figure>"
    )


# ``look`` is ``(ident, screen)`` as `_resolve_look` returned them.
def _exact_pic_body(look: tuple, page: _PageData, cap: _Caption) -> str:
    """The stored picture with its tap overlay, else the drawing of the screen."""
    ident, screen, label = look
    filename = str(((_SHOTS.get() or {}).get("frames") or {}).get(ident) or "")
    book = _IMAGES.get()
    if filename and book is not None:
        pic = (
            '<div class="picbox">'
            + _img_html(_shot_spec(filename, book, cap.text))
            + _tap_overlay(cap.target_id, screen)
            + "</div>"
        )
    else:
        pic = (
            '<div class="picdraw">'
            + screen_phone.compose(screen, esc=esc, app=page.app)
            + "</div>"
        )
        label += " · drawn from the element list, no picture stored"
    return pic + "<figcaption>" + label + "</figcaption></figure>"


def _same_gap(one: tuple, two: tuple) -> bool:
    """Both looks resolved to no picture, with the same verdict."""
    return one[2] == two[2] and one[2] != LOOK_EXACT


def _before_pic(before: object, page: _PageData, target: Any) -> str:
    """The Before picture of a step, or nothing when it has no before screen."""
    return _look_pic(before, page, _Caption("Before", target)) if before else ""


# Neither end has a picture and both miss it for the same reason: one
# note for the pair, not the same sentence twice side by side.
def _shared_note(before: object, after: object, library: object) -> bool:
    """Do both ends of a step lack a picture, for the same reason or the same key?"""
    return bool(before) and (
        before == after
        or bool(
            after
            and _same_gap(_resolve_look(before, library), _resolve_look(after, library))
        )
    )


def _step_pics(row: dict, page: _PageData, after_look: tuple) -> str:
    """The Before and After pictures of one step, or one shared note for both."""
    before = row.get("before_obs") or row.get("before")
    after = row.get("after_obs") or row.get("after")
    target: Any = row.get("target_id") if row.get("op") in TARGET_OPS else ""
    if after_look:
        return _before_pic(before, page, target) + (
            _look_pic(after_look[0], page, _Caption("After", note=after_look[1]))
        )
    if _shared_note(before, after, page.library):
        return _look_pic(before, page, _Caption("Before and after", target))
    return _before_pic(before, page, target) + (
        _look_pic(after, page, _Caption("After")) if after else ""
    )
