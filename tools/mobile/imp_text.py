"""Text normalisation shared by the case importers."""

from __future__ import annotations

import re

from tools.mobile.imp_consts import _BIDI_RE, MAX_CELL_CHARS


def fail(message: str) -> dict:
    return {"error": message, "content": None}


def _clean_document(text: object) -> str:
    """Normalise a WHOLE pasted document. Never truncates.

    ``_clean`` is a per-CELL helper and caps at ``MAX_CELL_CHARS``. Running it
    over an entire paste silently discarded everything past 8,000 characters:
    a 299-row table arrived as 100 rows and the other 199 were never mentioned
    -- silent row loss, which is the one thing this module exists to prevent.
    The document-level cap is ``MAX_BYTES`` and it REFUSES rather than trims.
    """
    body = text if isinstance(text, str) else ("" if text is None else str(text))
    return _BIDI_RE.sub("", body).replace("\r\n", "\n").replace("\r", "\n").strip()


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _clean(value: object) -> str:
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    text = _BIDI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > MAX_CELL_CHARS:
        text = text[:MAX_CELL_CHARS]
    return text.strip()
