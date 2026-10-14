"""One workbook part, size-capped and DOCTYPE-screened."""

from __future__ import annotations

import zipfile

from tools.mobile.imp_consts import _DECL_RE, MAX_UNCOMPRESSED_BYTES
from tools.mobile.imp_text import fail

_DOCTYPE_MSG = (
    "That workbook declares an XML DOCTYPE or ENTITY, which a real "
    "spreadsheet never does. It was rejected rather than parsed."
)


def _oversize_msg(name: str) -> str:
    return (
        "A part of that workbook ("
        + name
        + ") is larger than the allowed size and was not parsed."
    )


def _member_text(archive: zipfile.ZipFile, name: str) -> dict:
    try:
        with archive.open(name) as handle:
            raw = handle.read(MAX_UNCOMPRESSED_BYTES + 1)
    except KeyError:
        return {"error": None, "content": ""}
    if len(raw) > MAX_UNCOMPRESSED_BYTES:
        return fail(_oversize_msg(name))
    text = raw.decode("utf-8", errors="replace")
    if _DECL_RE.search(text):
        return fail(_DOCTYPE_MSG)
    return {"error": None, "content": text}
