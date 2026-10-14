"""Stdlib-only safe parsing helpers shared by shipped modules.

Leaf module: imports nothing from the project.
"""

from __future__ import annotations

import json
import re
from typing import NamedTuple
from xml.etree import ElementTree

XML_DECL_RE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


class JsonMode(NamedTuple):
    """Tolerance flags for ``loads_json`` (always built with keywords)."""

    # lstrip, then require a leading ``{`` or ``[`` (else None); a non-str input
    # raises AttributeError, as ``str.lstrip`` does.
    container_only: bool = False
    # return the value only when it is a dict, else None.
    dict_only: bool = False
    # ``(text or "").strip()`` (inside the try) instead of lstrip.
    strip_both: bool = False
    # catch Exception; otherwise only (ValueError, TypeError, RecursionError).
    swallow_all: bool = False


_PLAIN = JsonMode()
_TOLERATED = (ValueError, TypeError, RecursionError)


def _json_source(text: str, mode: JsonMode) -> str:
    if mode.container_only:
        return text.lstrip()
    return (text or "").strip() if mode.strip_both else text


def loads_json(text: str, mode: JsonMode = _PLAIN) -> object:
    """json.loads with the explicit tolerance flags both callers need.

    Returns None on any failure.
    """
    if mode.container_only and text.lstrip()[:1] not in "{[":
        return None
    try:
        obj = json.loads(_json_source(text, mode))
    except Exception as exc:
        if not (mode.swallow_all or isinstance(exc, _TOLERATED)):
            raise
        return None
    if mode.dict_only:
        return obj if isinstance(obj, dict) else None
    return obj


def has_xml_decl(text: str) -> bool:
    """True when *text* contains a DOCTYPE or ENTITY declaration."""
    return bool(XML_DECL_RE.search(text))


def parse_xml(text: str, *, refuse_decl: bool = False) -> ElementTree.Element | None:
    """ElementTree.fromstring that returns None on a ParseError.

    refuse_decl: also return None when *text* has a DOCTYPE/ENTITY declaration.
    Only ParseError is caught; any other exception propagates.
    """
    if refuse_decl and has_xml_decl(text):
        return None
    try:
        return ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return None
