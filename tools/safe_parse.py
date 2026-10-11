"""Stdlib-only safe parsing helpers shared by shipped modules.

Leaf module: imports nothing from the project.
"""

from __future__ import annotations

import json
import re
from xml.etree import ElementTree

XML_DECL_RE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


def loads_json(
    text: str,
    *,
    container_only: bool = False,
    dict_only: bool = False,
    strip_both: bool = False,
    swallow_all: bool = False,
) -> object:
    """json.loads with the explicit tolerance flags both callers need.

    container_only: lstrip, then require a leading ``{`` or ``[`` (else None);
        a non-str input raises AttributeError, as ``str.lstrip`` does.
    dict_only: return the value only when it is a dict, else None.
    strip_both: ``(text or "").strip()`` (inside the try) instead of lstrip.
    swallow_all: catch Exception; otherwise only (ValueError, TypeError,
        RecursionError).
    Returns None on any failure.
    """
    if container_only:
        t = text.lstrip()
        if t[:1] not in "{[":
            return None
    try:
        if not container_only:
            t = (text or "").strip() if strip_both else text
        obj = json.loads(t)
    except Exception as exc:
        if not swallow_all and not isinstance(
            exc, (ValueError, TypeError, RecursionError)
        ):
            raise
        return None
    if dict_only:
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
