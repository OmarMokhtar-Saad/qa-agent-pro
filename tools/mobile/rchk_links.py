"""The no-fetch pins: stray links, images and media outside the folder."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from tools.mobile import report
from tools.mobile.rchk_consts import EXTERNAL, PIN_NO_ASSET, SHOT_SRC
from tools.mobile.rchk_pins import _pin

if TYPE_CHECKING:
    from tools.mobile.rchk_page import _Page


# The ``src`` of every image that is neither the shot placeholder nor stored media.
#
# IDENTITY, not absence: see the note on EXTERNAL. TWO conditions, because
# "starts with ../" alone would admit "../../../etc" and every other escape
# out of the run folder -- a rule that passes on anything shaped vaguely
# right is the shape this repository keeps paying for. The prefix is the
# emitter's own constant, and the climb must be EXACTLY one.
#
# The one other home an image has is the report's own media folder -- a
# frame or a resized copy of one -- held to the media rule: inside
# it, with no climb at all. And THERE: frames used to travel inline, so a
# path under media/ that nothing wrote is the regression to catch, and a
# well-formed src that points at nothing passes every shape test.
def _stray_images(images: list, prefix: str, folder: Path | None) -> list:
    """The ``src`` of every image that is neither the shot placeholder nor media."""

    def _stored(src: str) -> bool:
        return folder is None or (folder / src).is_file()

    return [
        str(src)[:60]
        for src in images
        if not (
            (str(src).startswith(SHOT_SRC) and str(src).count("..") == 1)
            or (
                str(src).startswith(prefix)
                and ".." not in str(src)
                and _stored(str(src))
            )
        )
    ]


def _strays(page: "_Page", prefix: str, folder: Path | None) -> list:
    # EVERY link is a stray now. The shell used to carry three typeface links and this
    # filter existed to allow exactly those; they were deleted because a report is read
    # offline from a folder, so there is no longer a legitimate href on this page.
    strays = [str(href)[:60] for href in page.links]
    strays += _stray_images(page.images, prefix, folder)
    return strays


def _offfolder(media: list, prefix: str) -> list:
    return [
        tag + "@" + (str(src)[:60] or "(empty)")
        for tag, src in media
        if not str(src).startswith(prefix) or ".." in str(src)
    ]


# The page fetches NOTHING, images are inline, clips point INSIDE the folder.
#
# Four checks, one verdict, because they answer one question: can this report
# be read by a tester who is offline, or who was emailed it? A stray ``href``,
# an image ``src`` that is a PATH rather than a payload, a media ``src`` that
# is anything but a relative sibling under the report's own media directory,
# and any of the remaining fetch constructs each break that, and each is
# reported by name.
#
# The media half is a SOURCE test, not a text test, and it is judged off the
# parsed ``src`` so a screen that legitimately DISPLAYS the text of a URL
# cannot redden a pin on a healthy tree.
def _pin_links(page: "_Page", text: str, folder: Path | None = None) -> dict:
    """The page fetches nothing and every media source is inside the folder."""
    prefix = str(report.MEDIA_DIR) + "/"
    strays = _strays(page, prefix, folder)
    found = [needle for needle in EXTERNAL if needle in text]
    offfolder = _offfolder(page.media, prefix)
    return _pin(
        PIN_NO_ASSET,
        not strays and not found and not offfolder,
        "links off the font hosts="
        + ",".join(strays[:4])
        + " fetch constructs="
        + ",".join(found)
        + " media outside the folder="
        + ",".join(offfolder[:4]),
    )
