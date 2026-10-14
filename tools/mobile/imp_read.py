"""A path or a string -> text, byte-capped."""

from __future__ import annotations

from pathlib import Path

from tools.mobile.imp_consts import MAX_BYTES
from tools.mobile.imp_source import _maybe_path
from tools.mobile.imp_text import fail


def _too_big(what: str) -> str:
    return (
        str(what)
        + " is larger than the "
        + str(MAX_BYTES)
        + " byte cap and was not imported."
    )


def _read_bytes(source: bytes | bytearray) -> dict:
    if len(source) > MAX_BYTES:
        return fail(_too_big("input"))
    return {"error": None, "content": source.decode("utf-8", errors="replace")}


def _read_path(candidate: Path) -> dict:
    size = candidate.stat().st_size
    if size > MAX_BYTES:
        return fail(_too_big(str(candidate.name)))
    return {
        "error": None,
        "content": candidate.read_text(encoding="utf-8", errors="replace"),
    }


def _read_text(source: object) -> dict:
    """A path or a string -> text, byte-capped. Never raises."""
    try:
        if isinstance(source, (bytes, bytearray)):
            return _read_bytes(source)
        text = str(source or "")
        candidate = _maybe_path(text)
        if candidate is not None:
            return _read_path(candidate)
        if len(text.encode("utf-8", errors="replace")) > MAX_BYTES:
            return fail(_too_big("input"))
        return {"error": None, "content": text}
    except OSError as exc:
        return fail("Could not read that file: " + str(exc))
