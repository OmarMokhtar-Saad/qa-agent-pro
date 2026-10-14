"""Structural (JSON) redaction walk for tools.api_redact (C5 w16 leaf)."""

from __future__ import annotations

import json
import re

from config.settings_guard import never_raise
from tools.api_redact_desc import _redact_descriptors, _strip_sample_response
from tools.api_redact_keys import (
    _STRUCTURAL_BLOCK_KEYS,
    _STRUCTURAL_CHILD_ALLOW,
    _normalize_key,
    _redact_all_leaves,
    _redact_scalar,
    is_pointer,
    key_is_sensitive,
)
from tools.api_redact_text import _redact_value_patterns, _try_parse_json

_MARKER_ONLY_RE = re.compile(r"<redacted:[a-z_]+>")


# Value-shape redaction for a STRUCTURED string leaf, length-preserving.
# When the WHOLE value is a single shape (a 10-digit id that looks like a
# national id, a 16-digit reference that looks like a card), emit
# ``<redacted:string(N)>`` so the length/boundary the tester supplied survives
# (rev-6.3 M-3); a partial hit (a secret embedded in prose) keeps its context.
def _redact_value_shapes_structured(s: str) -> str:
    if is_pointer(s):
        return s
    # A string leaf that is ITSELF a JSON object/array (HAR postData.text,
    # Postman body.raw) is the most credential-dense part of a capture — recurse
    # it structurally rather than leave it to the value-shape scan (rev-6.4 H-2).
    nested = _try_parse_json(s)
    if nested is not None:
        with never_raise():
            return json.dumps(_redact_structural(nested), ensure_ascii=False)
        return f"<redacted:string({len(s)})>"
    red = _redact_value_patterns(s)
    if red == s:
        return s
    if _MARKER_ONLY_RE.fullmatch(red):
        return f"<redacted:string({len(s)})>"
    return red


def _redact_block(value: dict) -> dict:
    """Redact a structural block (auth/credentials): a scalar child survives
    only if its key is allowlisted; every other scalar child is redacted, and
    dict children recurse. Closes the smuggle-onto-auth.value path (H-1r)."""
    out: dict = {}
    for k, v in value.items():
        if isinstance(v, (dict, list)):
            out[k] = _redact_block(v) if isinstance(v, dict) else _redact_all_leaves(v)
        elif key_is_sensitive(k) or _normalize_key(k) not in _STRUCTURAL_CHILD_ALLOW:
            out[k] = _redact_scalar(v) if not is_pointer(v) else v
        elif isinstance(v, str) and not is_pointer(v):
            # An allowlisted child still gets the value-shape scan (no curl
            # flags), so a credential smuggled onto `mode`/`kind`
            # (`mode: "Basic <b64>"`) cannot survive on the key allowlist alone.
            out[k] = _redact_value_shapes_structured(v)
        else:
            out[k] = v
    return out


def redact_json_value(value: object, *, key: object = None) -> object:
    if key is not None and key_is_sensitive(key):
        # A sensitive key naming a known structural BLOCK with a dict value is
        # recursed with a child-key allowlist so `auth: {mode, kind}` survives
        # while `auth: "<secret>"` and `auth.value: "<secret>"` are redacted.
        if isinstance(value, dict) and _normalize_key(key) in _STRUCTURAL_BLOCK_KEYS:
            return _redact_block(value)
        return _redact_all_leaves(value)
    if isinstance(value, dict):
        return {k: redact_json_value(v, key=k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_json_value(v) for v in value]
    if isinstance(value, str) and not is_pointer(value):
        # Structured leaf: value shapes only, length-preserving, NO curl flags
        # (rev-6.3 M-1/M-3).
        return _redact_value_shapes_structured(value)
    return value


_SHAPE_TYPE_NAMES = {
    "string",
    "number",
    "integer",
    "boolean",
    "array",
    "object",
    "null",
}


def _lift_response_shape(plain: object) -> tuple[object, dict | None]:
    if isinstance(plain, dict) and isinstance(plain.get("success"), dict):
        rs = plain["success"].get("response_shape")
        if isinstance(rs, dict):
            plain["success"] = {
                k: v for k, v in plain["success"].items() if k != "response_shape"
            }
            return plain, rs
    return plain, None


def _reattach_response_shape(out: object, saved_shape: dict | None) -> None:
    if (
        saved_shape is not None
        and isinstance(out, dict)
        and isinstance(out.get("success"), dict)
    ):
        out["success"]["response_shape"] = {
            k: (
                v
                if isinstance(v, str) and v.strip().lower() in _SHAPE_TYPE_NAMES
                else redact_json_value(v, key=k)
            )
            for k, v in saved_shape.items()
        }


# success.response_shape is a closed TYPE vocabulary keyed by (often sensitive)
# field names; redacting its type values ("string" under key "nationalId") corrupts
# the grounding authority and makes the renderer refuse the whole suite (rev-3 H-B).
# So it is lifted out before redaction and re-attached afterwards: an entry whose
# value is a known type name survives verbatim, anything else is still redacted (a
# PHI value stuffed where a type belongs is still redacted IF its key is
# sensitive; a plain value under a non-sensitive key survives, but a non-type
# shape is refused by the renderer before any Java is produced — rev-4 L1).
def _redact_structural(obj: object) -> object:
    """Strip sample_response (any depth), then structural redaction: key-based
    (redact_json_value) + the {name, value} descriptor allowlist. Shared by the
    contract path and the parse-first intake path (rev-6.3 H-1)."""
    plain = json.loads(json.dumps(obj))
    plain = _strip_sample_response(plain)
    plain, saved_shape = _lift_response_shape(plain)
    out = _redact_descriptors(redact_json_value(plain))
    _reattach_response_shape(out, saved_shape)
    return out
