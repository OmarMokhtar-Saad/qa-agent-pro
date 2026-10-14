"""Report picture book: the per-build image state, its derivative cache and its notes."""

from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path

from tools.mobile import report_images, run_store
from tools.mobile.rshl_paths import MEDIA_DIR, report_path
from tools.mobile.rtxt_secret import esc

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


def _image_notes_html() -> str:
    """Every image note the pictures raised; build it after the journey and the
    case list, so all of them are in the book by now.
    """
    return "".join(
        "<li>" + esc(note, 300) + "</li>"
        for note in list((_IMAGES.get() or {}).get("notes") or [])
    )
