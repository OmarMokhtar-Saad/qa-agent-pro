"""Report media map: the stored-file names, the per-case clip map and the producer's notes."""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from tools.mobile import media
from tools.mobile.rshl_paths import MEDIA_DIR
from tools.mobile.rtxt_clean import _text
from tools.mobile.rtxt_secret import esc

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


# read it.
#
# The producer stores ``tc_id`` as a plain string; ``_text`` collapses
# whitespace and appends an ellipsis past its limit. Keying with ``str(...)``
# on one side and ``_text(..., 40)`` on the other agrees for every id there is
# today and diverges the day one is longer or carries a space -- one fact
# derived twice in two places, which is how a mirrored derivation drifts. The
# derivation lives here; both sides call it.
#
# **The id shape this assumes, stated because the key TRUNCATES.** ``_text``
# caps at 40 characters, so two ids sharing a 40-character prefix collide and
# the later record wins the slot. That is safe for the ids this lane actually
# produces and for no others: a ``tc_id`` is a short case identifier
# (``TC-001``). Both sides truncate identically, so a collision can only ever
# show the WRONG clip for a case, never a broken reference -- and if an id
# shape that is long and prefix-shared is ever introduced, this limit is the
# line to change, in this one place.
def _media_key(value: object) -> str:
    """The ONE normalisation of a media-map key, used to BUILD the map and to"""
    return _text(value, 40)


def _add_clips(clips: dict, case: Any) -> None:
    """File *case*'s clip records under its media key; a case with no usable id adds none."""
    if not isinstance(case, dict):
        return
    tc_id = _media_key(case.get("tc_id"))
    if not tc_id:
        return
    for rec in list(case.get("media") or []):
        if isinstance(rec, dict) and str(rec.get("kind") or "") == media.CLIP:
            clips.setdefault(tc_id, []).append(rec)


# **Clips only, and the frames are deliberately absent.** ``media`` writes a
# per-step FRAME record too, but this page already has one producer for "the
# device's own picture of this screen": :func:`_shot_html`, reading the
# per-observation PNGs :func:`_shots` funds, under the ambiguity rule
# :func:`_resolve_look` owns. A second frame path keyed on ``screen_id``
# would be a second answer to that one question -- and the wrong one on a
# conversation, where one ``screen_id`` covers several looks. So frames are
# read HERE by nobody, and the recording -- which no other producer has -- is
# what this map carries.
#
# Deriving it again inside a section would be two answers to one question,
# and mirrored derivations drift.
def _media_map(cases: Any) -> dict:
    """``{"clips": {tc_id: [rec, ...]}}``, derived ONCE and threaded down."""
    clips: dict = {}
    for case in list(cases or []):
        _add_clips(clips, case)
    for row in clips.values():
        row.sort(key=lambda rec: int(rec.get("seq") or 0))
    return {"clips": clips}


def _media_note(state: object) -> str:
    """The producer's own sentence for a state. NEVER this module's wording:
    ``media.NOTES`` is the one place a state is put into words, and a state
    with no entry is named rather than swallowed."""
    key = str(state or media.NOT_ATTEMPTED)
    return media.NOTES.get(key) or ("No clip for this step (" + esc(key, 40) + ").")


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


class _Step(NamedTuple):
    """One clip row: its label, its page-relative file ("" when absent) and the producer's note."""

    label: str
    src: str
    note: str


def _steps(tc_id: object, media_map: object) -> list | None:
    """The clip rows of one case, in order; None when it has no clips at all."""
    book = media_map if isinstance(media_map, dict) else {}
    rows = (book.get("clips") or {}).get(_media_key(tc_id)) or []
    if not rows:
        return None
    return [
        _Step(
            "Step " + str(int(rec.get("seq") or 0) + 1),
            _media_src(rec),
            _media_note(rec.get("state")),
        )
        for rec in rows
        if isinstance(rec, dict)
    ]
