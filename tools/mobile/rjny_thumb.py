"""The film-strip thumbnail of a turn."""

from __future__ import annotations

from tools.mobile import run_store
from tools.mobile.report_frames import _SHOTS, LOOK_EXACT, _resolve_look
from tools.mobile.report_media import _IMAGES, THUMB_SIZES, _img_html, _ImgSpec
from tools.mobile.report_shell import MEDIA_DIR
from tools.mobile.rjny_args import _Thumb


def _frame_img(n: int, frame: dict, book: dict) -> str:
    """The thumbnail cut from a turn's own frame."""
    name = str(frame.get("file"))
    return _img_html(
        _ImgSpec(
            MEDIA_DIR,
            name,
            book["media_dir"] / name,
            "Turn " + str(n),
            THUMB_SIZES,
        )
    )


def _look_img(f: dict, thumb: _Thumb, book: dict) -> str:
    """The thumbnail of the last look the case recorded, or ``""`` when it is not an exact one."""
    ident, screen, verdict = _resolve_look(f["last_look"] or f["last"], thumb.library)
    filename = str(((_SHOTS.get() or {}).get("frames") or {}).get(ident) or "")
    if screen is None or verdict != LOOK_EXACT or not filename:
        return ""
    return _img_html(
        _ImgSpec(
            "../" + run_store.SHOTS_DIR,
            filename,
            book["shots_dir"] / filename,
            "Turn " + str(thumb.n),
            THUMB_SIZES,
        )
    )


def _thumb_img(f: dict, thumb: _Thumb) -> str:
    """The picture of a thumbnail: the turn's frame, else its last look, else nothing."""
    book = _IMAGES.get()
    if book is None:
        return ""
    return (
        _frame_img(thumb.n, thumb.frame, book)
        if thumb.frame
        else _look_img(f, thumb, book)
    )


def _thumb_html(f: dict, thumb: _Thumb) -> str:
    img = _thumb_img(f, thumb)
    return (
        '<li><a class="thumb" href="#turn-'
        + f["slug"]
        + '">'
        + (img or '<span class="thumbgap">no picture</span>')
        + "<span>End of goal · turn "
        + str(int(thumb.n))
        + "</span></a></li>"
    )
