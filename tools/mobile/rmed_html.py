"""Report media HTML: the step clips, the responsive picture and the turn recording."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tools.mobile import report_images
from tools.mobile.rmed_book import _IMAGES, _image_note, _variants
from tools.mobile.rmed_map import _media_note, _media_src, _steps
from tools.mobile.rshl_paths import MEDIA_DIR
from tools.mobile.rtxt_secret import esc

#: What a clip IS, as distinct from how it went.
#:
#: A SEPARATE note from :func:`_media_note`, and separate on purpose: "the
#: recorder refused this step" and "every clip on this page is smaller than the
#: screen was" are two different facts about the run, and one sentence carrying
#: both is the half-wired note this repository has already paid for. The same
#: three-note rule ``_shot_html`` follows for a missing picture and a dropped
#: one.
#:
#: MEASURED, not assumed (2026-09-10, emulator-5554, API 35): the device's
#: encoder refuses the panel's native 1440x3120 and silently retries at
#: 720x1280, so ``adb.SCREENRECORD_SIZE`` picks the size instead. The size is
#: NAMED rather than restated here, so this sentence cannot go stale the day
#: that constant changes.
CLIP_SCALE_NOTE = (
    "This clip was recorded at a reduced size, not at the resolution the device "
    "drew: the encoder refuses the screen's own size, so the lane chooses one "
    "(adb.SCREENRECORD_SIZE). It shows WHAT happened, not how sharp the app "
    "looked; the pictures and the element map on this page carry the detail."
)


def _video_tag(head: str, src: str) -> str:
    """*head* (the opening ``<video>`` tag) plus the clip source; NEVER an empty player."""
    # NEVER an empty <video>: a player with no source reads as a broken
    # report, and the reason is the only thing worth showing here.
    if not src:
        return ""
    return head + '<source src="' + esc(src, 140) + '" type="video/mp4"></video>'


def _scale_tag(head: str, src: str) -> str:
    """The clip-scale note after *head*, only where a clip plays: no file, no downscale."""
    return head + esc(CLIP_SCALE_NOTE, 300) + "</p>" if src else ""


# **The class is ``stepclip``, not ``clip``.** ``.clip`` is ALREADY taken in
# ``report_shell.html`` (the code-block truncation bar, which the shell's JS
# selects by name) and ``tools/mobile_evidence/exchanges.py`` emits it onto
# THIS SAME PAGE; a second ``.clip`` rule would repaint those notes.
# ``tests/mobile/test_mobile_report_css_binding.py`` asserts every class
# name introduced here was UNSTYLED before it landed.
def _video_html(tc_id: object, media_map: object) -> str:
    """Every clip of this case, in order, or the named reason for each gap."""
    steps = _steps(tc_id, media_map)
    if steps is None:
        return ""
    out = [
        f'<div class="stepcliprow"><h4>{esc(s.label, 24)}</h4>'
        + _video_tag(
            '<video class="stepclip" controls preload="metadata" playsinline>', s.src
        )
        + f'<p class="stepclipnote">{esc(s.note, 300)}</p>'
        + _scale_tag('<p class="stepclipnote">', s.src)
        + "</div>"
        for s in steps
    ]
    return '<div class="stepclips">' + "".join(out) + "</div>"


@dataclass(frozen=True)
class _ImgSpec:
    """One ``<img>``: the original's folder and name, its path on disk, alt text and sizes."""

    rel_dir: str
    name: str
    path: Path
    alt: str
    sizes: str


def _marks(spec: _ImgSpec, got: dict) -> dict:
    """The escaped ``src``/``srcset`` values of the derivatives of *spec*."""
    # Derivatives always live in the report's media folder; the original
    # stays where it is (a screenshot sits in ../shots beside the report).
    return report_images.srcset_markup(
        esc,
        report_images._Srcset(
            MEDIA_DIR,
            got.get("hash"),
            got.get("widths"),
            spec.name,
            got.get("width"),
            spec.rel_dir,
        ),
    )


def _srcset_attr(marks: dict, sizes: str) -> str:
    """The ``srcset``/``sizes`` attributes, only when there is more than one candidate."""
    if "," not in marks["srcset"]:
        return ""
    return ' srcset="' + marks["srcset"] + '" sizes="' + esc(sizes, 80) + '"'


def _dims_attr(got: dict) -> str:
    """The ``width``/``height`` attributes when both are known."""
    width, height = got.get("width"), got.get("height")
    if not (width and height):
        return ""
    return ' width="' + str(int(width)) + '" height="' + str(int(height)) + '"'


def _first_image(book: dict | None) -> bool:
    """Whether this is the page's first image; every call counts one."""
    first = book is not None and not book["seen"]
    if book is not None:
        book["seen"] += 1
    return first


def _img_tail(spec: _ImgSpec, first: bool) -> str:
    """Loading hint, alt text and ``data-full`` (the original) closing the tag."""
    return (
        (' fetchpriority="high"' if first else ' loading="lazy"')
        + ' decoding="async" alt="'
        + esc(spec.alt, 160)
        + '" data-full="'
        + esc(spec.rel_dir.rstrip("/") + "/" + spec.name, 200)
        + '">'
    )


def _poster_attr(body: dict) -> str:
    """The ``poster`` attribute of a clip, or ``""`` when no poster could be made."""
    book = _IMAGES.get()
    if book is None:
        return ""
    got = report_images.ensure_poster(
        book["media_dir"] / str(body.get("file")), book["media_dir"]
    )
    _image_note(got.get("error"))
    if not got.get("path"):
        return ""
    return ' poster="' + esc(MEDIA_DIR + "/" + str(got["path"]), 200) + '"'


def _img_html(spec: _ImgSpec) -> str:
    """One ``<img>`` with its WebP ``srcset``; ``data-full`` is the original."""
    # Every value is escaped by esc, inside srcset_markup or here. The first
    # image of the page loads eagerly; every other one is lazy.
    book = _IMAGES.get()
    got = _variants(spec.path)
    marks = _marks(spec, got)
    _image_note(marks.get("error"))
    first = _first_image(book)
    return (
        '<img src="'
        + marks["src"]
        + '"'
        + _srcset_attr(marks, spec.sizes)
        + _dims_attr(got)
        + _img_tail(spec, first)
    )


def _clip_html(rec: object) -> str:
    """A turn's recording with its poster, or the producer's reason for none."""
    body = rec if isinstance(rec, dict) else {}
    srcpath = _media_src(body)
    if not srcpath:
        return '<p class="clipgap">' + esc(_media_note(body.get("state")), 300) + "</p>"
    return (
        '<figure class="turnclip"><video controls preload="metadata" playsinline'
        + _poster_attr(body)
        + '><source src="'
        + esc(srcpath, 140)
        + '" type="video/mp4"></video><figcaption>Recording of this turn</figcaption></figure>'
    )
