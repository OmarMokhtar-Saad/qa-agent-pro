"""Report text coercion: caps, tokens, slugs and guard-marker neutralising."""

from __future__ import annotations

import re

from tools.untrusted import GUARD_MARKERS, NEUTRALIZED

MAX_TEXT = 200


_SAFE_TOKEN = re.compile(r"[^a-z0-9_]+")


_SLUG = re.compile(r"[^A-Za-z0-9_-]+")


def _text(value: object, limit: int = MAX_TEXT) -> str:
    """Coerce, drop control characters, flatten whitespace, cap.

    Independent of ``perception._clean``: by the time this module reads a store
    file, that file is an outside input again.
    """
    raw = "" if value is None else str(value)
    kept = "".join(
        " " if character in "\t\r\n" else character
        for character in raw
        if character == " " or character.isprintable()
    )
    kept = " ".join(kept.split())
    if len(kept) > limit:
        kept = kept[:limit] + "..."
    return kept


def _neutralize_markers(text: str) -> str:
    """Blunt an untrusted-content or guard marker carried in a store value.

    ``perception`` does this for SCREEN attributes, but a case's title, reason
    and trace detail never pass through it -- they come back off disk, which
    :func:`_text`'s docstring already calls an outside input again. So a hostile
    app label reached the page with the sentinel intact, and this module's own
    selfcheck caught it on a real run while 937 tests stayed green, because no
    fixture had put the sentinel in a case field.

    The markers are IMPORTED, never restated. A second copy of that literal is
    the drift already fixed once inside ``perception``, where a hardcoded copy
    would have stopped matching a reworded guard with no test noticing.
    """
    out = text
    for marker in GUARD_MARKERS:
        if marker and marker in out:
            out = out.replace(marker, NEUTRALIZED)
    return out


def _token(value: object) -> str:
    """A store string reduced to something safe to use as a class or attribute.

    A verdict comes from a file, and this module puts it in a CLASS name and in
    a ``data-`` ATTRIBUTE NAME. An attribute name cannot be escaped, so it is
    normalised instead: ``[a-z0-9_]`` only, capped, ``unknown`` when nothing
    survives.
    """
    reduced = _SAFE_TOKEN.sub("_", _text(value, 40).lower()).strip("_")
    return reduced[:24] or "unknown"


def _slug(value: object) -> str:
    """A case id as a DOM id / URL fragment: ``[A-Za-z0-9_-]`` only, capped."""
    reduced = _SLUG.sub("-", _text(value, 40)).strip("-")
    return reduced[:40] or "case"
