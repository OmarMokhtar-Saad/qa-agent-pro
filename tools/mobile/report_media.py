"""Media lookup and rendering for the report: video and image maps, notes, thumbnails and clips."""

from __future__ import annotations

import re
from contextvars import ContextVar
from pathlib import Path

from tools.mobile import media, report_images, run_store
from tools.mobile.report_shell import MEDIA_DIR, report_path
from tools.mobile.report_text import _text, esc

#: A stored media file name, as this page may reference it. Names are produced
#: by ``media._safe`` (``A-Za-z0-9_.-``) and this is the CONSUMER side of that
#: one rule: a name that does not match is not referenced at all.
#:
#: Deliberately NOT ``report._slug``: that is a FUNCTION, not a compiled
#: pattern (``_SLUG`` is the pattern), and it rewrites ``.`` to ``-`` -- so
#: using it here would both raise ``AttributeError`` on every render that has a
#: media file and, once "fixed" by calling it, point every reference at a file
#: name that does not exist.
_MEDIA_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")


def _media_src(rec: object) -> str:
    """``media/<file>`` for a record with a usable file name, else ``""``."""
    body = rec if isinstance(rec, dict) else {}
    name = str(body.get("file") or "")
    if not name or ".." in name or not _MEDIA_NAME.match(name):
        return ""
    return MEDIA_DIR + "/" + name


def _media_key(value: object) -> str:
    """The ONE normalisation of a media-map key, used to BUILD the map and to
    read it.

    The producer stores ``tc_id`` as a plain string; ``_text`` collapses
    whitespace and appends an ellipsis past its limit. Keying with ``str(...)``
    on one side and ``_text(..., 40)`` on the other agrees for every id there is
    today and diverges the day one is longer or carries a space -- one fact
    derived twice in two places, which is how a mirrored derivation drifts. The
    derivation lives here; both sides call it.

    **The id shape this assumes, stated because the key TRUNCATES.** ``_text``
    caps at 40 characters, so two ids sharing a 40-character prefix collide and
    the later record wins the slot. That is safe for the ids this lane actually
    produces and for no others: a ``tc_id`` is a short case identifier
    (``TC-001``). Both sides truncate identically, so a collision can only ever
    show the WRONG clip for a case, never a broken reference -- and if an id
    shape that is long and prefix-shared is ever introduced, this limit is the
    line to change, in this one place.
    """
    return _text(value, 40)


def _media_map(cases: object) -> dict:
    """``{"clips": {tc_id: [rec, ...]}}``, derived ONCE and threaded down.

    **Clips only, and the frames are deliberately absent.** ``media`` writes a
    per-step FRAME record too, but this page already has one producer for "the
    device's own picture of this screen": :func:`_shot_html`, reading the
    per-observation PNGs :func:`_shots` funds, under the ambiguity rule
    :func:`_resolve_look` owns. A second frame path keyed on ``screen_id``
    would be a second answer to that one question -- and the wrong one on a
    conversation, where one ``screen_id`` covers several looks. So frames are
    read HERE by nobody, and the recording -- which no other producer has -- is
    what this map carries.

    Deriving it again inside a section would be two answers to one question,
    and mirrored derivations drift.
    """
    clips: dict = {}
    for case in list(cases or []):
        if not isinstance(case, dict):
            continue
        tc_id = _media_key(case.get("tc_id"))
        if not tc_id:
            continue
        for rec in list(case.get("media") or []):
            if not isinstance(rec, dict):
                continue
            if str(rec.get("kind") or "") == media.CLIP:
                clips.setdefault(tc_id, []).append(rec)
    for row in clips.values():
        row.sort(key=lambda rec: int(rec.get("seq") or 0))
    return {"clips": clips}


def _media_note(state: object) -> str:
    """The producer's own sentence for a state. NEVER this module's wording:
    ``media.NOTES`` is the one place a state is put into words, and a state
    with no entry is named rather than swallowed."""
    key = str(state or media.NOT_ATTEMPTED)
    return media.NOTES.get(key) or ("No clip for this step (" + esc(key, 40) + ").")


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


def _video_html(tc_id: object, media_map: object) -> str:
    """Every clip of this case, in order, or the named reason for each gap.

    **The class is ``stepclip``, not ``clip``.** ``.clip`` is ALREADY taken in
    ``report_shell.html`` (the code-block truncation bar, which the shell's JS
    selects by name) and ``tools/mobile_evidence/exchanges.py`` emits it onto
    THIS SAME PAGE; a second ``.clip`` rule would repaint those notes.
    ``tests/mobile/test_mobile_report_css_binding.py`` asserts every class
    name introduced here was UNSTYLED before it landed.
    """
    book = media_map if isinstance(media_map, dict) else {}
    rows = (book.get("clips") or {}).get(_media_key(tc_id)) or []
    if not rows:
        return ""
    out = []
    for rec in rows:
        if not isinstance(rec, dict):
            continue
        label = "Step " + str(int(rec.get("seq") or 0) + 1)
        srcpath = _media_src(rec)
        if srcpath:
            body = (
                '<video class="stepclip" controls preload="metadata" playsinline>'
                '<source src="' + esc(srcpath, 140) + '" type="video/mp4"></video>'
            )
        else:
            # NEVER an empty <video>: a player with no source reads as a broken
            # report, and the reason is the only thing worth showing here.
            body = ""
        # TWO notes, never one: how this step's recording went, and what every
        # recording here IS. The scale note shows ONLY where a clip plays: a
        # step with no file was not downscaled, it was not recorded.
        scale = (
            '<p class="stepclipnote">' + esc(CLIP_SCALE_NOTE, 300) + "</p>"
            if srcpath
            else ""
        )
        out.append(
            '<div class="stepcliprow"><h4>'
            + esc(label, 24)
            + "</h4>"
            + body
            + '<p class="stepclipnote">'
            + esc(_media_note(rec.get("state")), 300)
            + "</p>"
            + scale
            + "</div>"
        )
    return '<div class="stepclips">' + "".join(out) + "</div>"


#: This build's picture book: where the report's media and the run's shots
#: live, the derivatives made so far, how many images were emitted (the first
#: loads eagerly) and every image note Diagnostics must print. Set by
#: :func:`_document` and reset in its ``finally``, like :data:`_SHOTS`.
_IMAGES: ContextVar = ContextVar("_IMAGES", default=None)


THUMB_SIZES = "(max-width: 600px) 30vw, 160px"


def _image_book(run_id: object) -> dict:
    target = report_path(str(run_id))
    return {
        "media_dir": target.parent / MEDIA_DIR,
        "shots_dir": target.parent.parent / run_store.SHOTS_DIR,
        "variants": {},
        "notes": [],
        "seen": 0,
    }


def _image_note(text: object) -> None:
    book = _IMAGES.get()
    if book is not None and text and str(text) not in book["notes"]:
        book["notes"].append(str(text))


def _variants(path: Path) -> dict:
    """``report_images.ensure_variants`` once per file per build."""
    book = _IMAGES.get()
    if book is None:
        return {}
    key = str(path)
    if key not in book["variants"]:
        got = report_images.ensure_variants(path, book["media_dir"])
        _image_note(got.get("error"))
        book["variants"][key] = got
    return book["variants"][key]


def _img_html(rel_dir: str, name: str, path: Path, alt: str, sizes: str) -> str:
    """One ``<img>`` with its WebP ``srcset``; ``data-full`` is the original.

    Every value is escaped by :func:`esc`, inside ``srcset_markup`` or here.
    The first image of the page loads eagerly; every other one is lazy.
    """
    book = _IMAGES.get()
    got = _variants(path)
    # Derivatives always live in the report's media folder; the original
    # stays where it is (a screenshot sits in ../shots beside the report).
    marks = report_images.srcset_markup(
        esc,
        MEDIA_DIR,
        got.get("hash"),
        got.get("widths"),
        name,
        got.get("width"),
        original_dir=rel_dir,
    )
    _image_note(marks.get("error"))
    first = book is not None and not book["seen"]
    if book is not None:
        book["seen"] += 1
    width, height = got.get("width"), got.get("height")
    return (
        '<img src="'
        + marks["src"]
        + '"'
        + (
            (' srcset="' + marks["srcset"] + '" sizes="' + esc(sizes, 80) + '"')
            if "," in marks["srcset"]
            else ""
        )
        + (
            (' width="' + str(int(width)) + '" height="' + str(int(height)) + '"')
            if width and height
            else ""
        )
        + (' fetchpriority="high"' if first else ' loading="lazy"')
        + ' decoding="async" alt="'
        + esc(alt, 160)
        + '" data-full="'
        + esc(rel_dir.rstrip("/") + "/" + name, 200)
        + '">'
    )


def _clip_html(rec: object) -> str:
    """A turn's recording with its poster, or the producer's reason for none."""
    body = rec if isinstance(rec, dict) else {}
    srcpath = _media_src(body)
    if not srcpath:
        return '<p class="clipgap">' + esc(_media_note(body.get("state")), 300) + "</p>"
    poster = ""
    book = _IMAGES.get()
    if book is not None:
        got = report_images.ensure_poster(
            book["media_dir"] / str(body.get("file")), book["media_dir"]
        )
        _image_note(got.get("error"))
        if got.get("path"):
            poster = ' poster="' + esc(MEDIA_DIR + "/" + str(got["path"]), 200) + '"'
    return (
        '<figure class="turnclip"><video controls preload="metadata" playsinline'
        + poster
        + '><source src="'
        + esc(srcpath, 140)
        + '" type="video/mp4"></video><figcaption>Recording of this turn</figcaption></figure>'
    )


def _turn_frame(tc_id: object, cases_by_id: dict) -> dict:
    """The turn's full-size frame record from the media lane, or ``{}``."""
    case = cases_by_id.get(_media_key(tc_id)) or {}
    for rec in list(case.get("media") or []):
        if (
            isinstance(rec, dict)
            and str(rec.get("kind") or "") == media.FRAME
            and _media_src(rec)
        ):
            return rec
    return {}


def _image_notes_html() -> str:
    """Every image note the pictures raised; build it after the journey and the
    case list, so all of them are in the book by now.
    """
    return "".join(
        "<li>" + esc(note, 300) + "</li>"
        for note in list((_IMAGES.get() or {}).get("notes") or [])
    )
