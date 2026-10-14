"""Stored screenshots on the report page: the per-build frame book and its markup."""

from __future__ import annotations

import logging
from contextvars import ContextVar

from tools.mobile import run_store
from tools.mobile.report_text import esc

# same logger as the old module; kept for log filters
logger = logging.getLogger("tools.mobile.report")


#: This document's resolved frames, ``{"frames": {key: filename}}``.
#:
#: A ContextVar rather than a parameter for the reason ``mcp_handlers``'
#: ``_MOBILE_IMAGE_SPECS`` is one: the value is decided ONCE at the top of the
#: build and read in exactly one leaf (:func:`_shot_html`) that four call sites
#: reach through three intermediate builders, so threading it would rewrite five
#: signatures for a value none of them use. Two reports rendered concurrently
#: cannot see each other's frames.
_SHOTS: ContextVar = ContextVar("_SHOTS", default=None)


#: The ``src`` prefix every ``img`` on this page carries, and THE definition of it.
#:
#: A FOLDER-RELATIVE PATH, not a ``data:`` payload. The index lives in ``report/``
#: inside the run directory and the PNGs live in ``../shots/``, so the whole folder
#: travels as one artifact -- which is already the documented contract (the
#: recordings under ``report/media/`` have always worked this way). A lone
#: ``index.html``, separated from its folder, shows no screens; the page says so in
#: its own standfirst rather than rendering broken images silently.
#:
#: Declared here, in the emitter, and IMPORTED by the guard
#: (``report_selfcheck.SHOT_SRC = report.SHOT_SRC``) rather than restated there.
#: The direction is forced: ``report_selfcheck`` already does
#: ``from tools.mobile import report``, so the reverse import would be a cycle.
#: One object, one meaning -- this very change inverted the MEANING of the value
#: while the name stayed, and every consumer that bound the constant rather than
#: the literal needed no edit at all. That is the property, demonstrated.
SHOT_SRC = "../" + run_store.SHOTS_DIR + "/"


#: An observation for which NOTHING was ever stored.
#:
#: A DIFFERENT name and different words from the note below, on purpose: "no
#: picture was ever taken" and "a picture exists and this page declined it" are
#: different facts about the run and only one of them is worth investigating.
#: Collapsing them is the half-wired-sentinel failure this repository has
#: already paid for -- the same three-state rule ``tools/mobile/screenshot.py``
#: states for the packet's own note.
SHOT_MISSING_NOTE = (
    "No picture of this screen was stored: the capture did not succeed on that "
    "turn, or this run is older than stored frames. The drawing below is "
    "composed from the element list, which is all this page has."
)


# ONE producer for every picture on this page. It answers exactly one question --
# "does a non-empty PNG exist on disk for this observation?" -- by reading the
# file, so the page can never claim a picture that is not there.
#
# *library* is the MERGED book :func:`_merged_library` builds -- screens AND
# observations -- because that is what a frame resolves against, and a frame the
# page cannot resolve is a frame no picture belongs under.
#
# THERE IS NO BUDGET ANY MORE. Nothing is inlined, so the page costs the same
# whether it references one picture or two hundred, and a "this report declined
# to show it" state has no producer left. It was DELETED rather than kept as an
# always-empty list: a sentence with no producer is a fabricated absence, and an
# always-empty branch is how a dead state survives a rewrite.
def _shots(run_id: object, library: object) -> dict:
    """``{"frames": {key: filename}}``. Never raises."""
    frames: dict = {}
    try:
        book = library if isinstance(library, dict) else {}
        for ident in sorted(str(key) for key in book):
            data = (run_store.read_shot(str(run_id), ident) or {}).get("content")
            if not isinstance(data, (bytes, bytearray)) or not data:
                continue
            frames[ident] = ident + ".png"
    except Exception:  # never-raise: a picture is not a verdict
        logger.exception("mobile.report._shots failed")
    return {"frames": frames}


# *ident* is what :func:`_resolve_look` RESOLVED, and this function is called only
# on its ``LOOK_EXACT`` answer. That is load-bearing: under ``LOOK_AMBIGUOUS`` the
# key is a bare screen id with two or more stored looks behind it, and a picture
# drawn there would be one arbitrary look captioned as the step's own -- the
# fabricated-evidence defect ``_resolve_look`` exists to stop, in a form a tester
# is even less able to question because it is a photograph.
#
# A PATH and never a ``data:`` URI: see :data:`SHOT_SRC`. The self-check enforces
# that every ``img`` on the page starts with that prefix and climbs exactly one
# level -- ``report_selfcheck._pin_links`` checks IDENTITY, as it already does for
# the shell's one script and for every clip source.
#
# The ``<img>`` is emitted ONLY from a non-empty entry in ``frames``, which only a
# non-empty file on disk can produce, so a capture that failed can never leave
# this page claiming a picture exists.
def _shot_html(ident: str) -> str:
    """The stored PNG of *ident*, referenced by path, or the honest note for its absence."""
    book = _SHOTS.get() or {}
    filename = str((book.get("frames") or {}).get(ident) or "")
    if filename:
        return (
            '<figure class="shot"><img alt="The screen as the device drew it" '
            'loading="lazy" src="' + SHOT_SRC + filename + '"></figure>'
        )
    return '<p class="wirenote shotnote">' + esc(SHOT_MISSING_NOTE, 300) + "</p>"
