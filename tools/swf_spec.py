"""OpenAPI/Swagger URL recognition and spec-text parsing (leaf of ``tools.swagger_fetcher``)."""

from __future__ import annotations

import json
from urllib.parse import urlparse

import yaml

# URL substrings that mark a likely OpenAPI/Swagger document.
_URL_HINTS = ("swagger", "openapi", "api-docs", "api_docs")
_SPEC_SUFFIXES = (".json", ".yaml", ".yml")


def looks_like_openapi_url(url: str) -> bool:
    """Cheap pre-filter: does this URL plausibly point at an OpenAPI spec?

    Used by app.py to decide whether to try the spec fetch before the generic
    HTML page fetch. False negatives just fall back to the page path; false
    positives are caught by the parse step (which then falls back too).
    """
    try:
        parsed = urlparse((url or "").strip().lower())
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    haystack = parsed.path + "?" + parsed.query
    if any(hint in haystack for hint in _URL_HINTS):
        return True
    return parsed.path.endswith(_SPEC_SUFFIXES)


def _load(text: str) -> object:
    """JSON first, then YAML; ``None`` when neither parses."""
    try:
        return json.loads(text)
    except ValueError:
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError:
            return None


def _spec_or_none(data: object) -> dict | None:
    """*data* when it is a mapping with a version marker and a ``paths`` mapping."""
    if not isinstance(data, dict):
        return None
    if "openapi" not in data and "swagger" not in data:
        return None
    if not isinstance(data.get("paths"), dict):
        return None
    return data


def _parse_spec(text: str) -> dict | None:
    """Parse JSON-or-YAML text into an OpenAPI/Swagger dict, else ``None``.

    Accepts only mappings carrying an ``openapi``/``swagger`` version marker
    and a ``paths`` mapping — anything else is "not a spec", never an error.
    """
    if not text or not text.strip():
        return None
    return _spec_or_none(_load(text))
