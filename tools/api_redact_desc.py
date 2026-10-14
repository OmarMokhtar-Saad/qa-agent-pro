"""Descriptor ({name, value}) redaction for tools.api_redact (C5 w16 leaf)."""

from __future__ import annotations

from tools.api_redact_keys import (
    _redact_scalar,
    header_value_survives,
    is_pointer,
    key_is_sensitive,
)

_VALUE_SIBLING_KEYS = ("value", "example")
# Descriptor lists whose entries are HEADERS (allowlist: redact unless the name
# is one of the four safe headers). Postman uses singular ``header``.
_HEADER_LIST_KEYS = ("headers", "header")
# The field naming a descriptor: our template uses ``name``; Postman/HAR use
# ``key`` (``{"key":"Authorization","value":…}``) — both are handled (H-1).
_DESCRIPTOR_NAME_KEYS = ("name", "key")
_MARKER_PREFIX = "<redacted:"


def _already_redacted(v: object) -> bool:
    return isinstance(v, str) and v.startswith(_MARKER_PREFIX)


def _strip_sample_response(value: object) -> object:
    if isinstance(value, dict):
        return {
            k: _strip_sample_response(v)
            for k, v in value.items()
            if k != "sample_response"
        }
    if isinstance(value, list):
        return [_strip_sample_response(v) for v in value]
    return value


def _name_needs_redaction(name: object, header_ctx: bool) -> bool:
    if header_ctx:
        return not header_value_survives(name)
    return key_is_sensitive(name)


def _redact_sibling_values(out: dict) -> None:
    for sib in _VALUE_SIBLING_KEYS:
        # Skip a value already redacted upstream — re-redacting a
        # ``<redacted:string(10)>`` marker inflates its length and
        # loses the boundary the tester supplied (rev-6.4 M-1).
        if sib in out and not is_pointer(out[sib]) and not _already_redacted(out[sib]):
            out[sib] = _redact_scalar(out[sib])


def _redact_descriptors(value: object, *, header_ctx: bool = False) -> object:
    if isinstance(value, dict):
        out = {
            k: _redact_descriptors(v, header_ctx=(str(k) in _HEADER_LIST_KEYS))
            for k, v in value.items()
        }
        name = next(
            (out[k] for k in _DESCRIPTOR_NAME_KEYS if out.get(k) is not None), None
        )
        if name is not None and _name_needs_redaction(name, header_ctx):
            _redact_sibling_values(out)
        return out
    if isinstance(value, list):
        return [_redact_descriptors(v, header_ctx=header_ctx) for v in value]
    return value
