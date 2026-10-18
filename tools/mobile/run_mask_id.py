"""Mask national-ID-like values, redact credentials and blank password nodes at the WRITE.

Used by :func:`tools.mobile.run_redact.redact`, the one choke point every run
artefact passes (manifest, cases, screens, observations, report inputs).

* An ID-like digit run (8+ digits) in a typed value, in an editable node's text
  or in title/finding/summary text is MASKED to its last four digits (``****7087``): a
  tester can still tell two IDs apart. Digit runs inside longer tokens, hex
  hashes, ``bounds`` and decimals (timestamps) are not touched.
* A typed value into a password, OTP or PIN field is REDACTED, never masked: a
  masked password still leaks its length and tail (Android log-info-disclosure
  guidance: mask identifiers, redact secrets).
* A password node (``secure`` / ``password=true`` / ``isPassword``) has its
  text blanked, so a saved UI dump never carries what the field held.

Only the written copy changes; the run's in-memory state is untouched.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from tools.mobile import run_consts

_ID_RE = re.compile(r"(?<![\w.-])\d{8,}(?![\w-]|\.\d)")
# A Saudi National ID / Iqama: 10 digits starting 1 or 2, optional single space or
# dash between digits, any letter/underscore/dash prefix. Never inside a longer digit
# run, a decimal, or after a digit group ("+1 202 555 0123", timestamps). A date
# ("2026-10-18 20:29", "18-10-2026 12 runs") is no ID, whatever number follows it.
_DATE_AHEAD = r"(?!\d{4}[-/]\d\d?[-/]\d\d?(?!\d)|\d\d?[-/]\d\d?[-/]\d{4}(?!\d))"
_SHAPE_RE = re.compile(
    r"(?<![\d.])(?<!\d[ -])(?<![A-Za-z]{7})"
    + _DATE_AHEAD
    + r"[12](?:[ -]?\d){9}(?![A-Za-z\d]|\.\d|[ -]\d)"
)
_SECRET_FIELD_RE = re.compile(r"(?<![a-z])(otp|pin|cvv|passw\w*|passcode)(?![a-z])")
_TYPED_OPS = frozenset({"type", "fill"})
_PROSE_KEYS = frozenset({"title", "reason", "finding", "detail", "note", "summary"})
_NAME_KEYS = ("label", "rid", "hint", "desc", "name", "resource_id", "field")
_PASSWORD_FLAGS = frozenset({"secure", "password", "ispassword", "is_password"})


def mask_id(text: str) -> str:
    """*text* with every ID-like digit run cut to ``****`` + its last four digits."""
    text = _SHAPE_RE.sub(lambda m: "****" + re.sub(r"\D", "", m.group(0))[-4:], text)
    return _ID_RE.sub(lambda m: "****" + m.group(0)[-4:], text)


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


def _is_password_node(out: dict) -> bool:
    return any(
        str(key).lower() in _PASSWORD_FLAGS and _truthy(value)
        for key, value in out.items()
    )


def _secret_field(out: dict) -> bool:
    names = " ".join(str(out.get(key) or "") for key in _NAME_KEYS).lower()
    return bool(_SECRET_FIELD_RE.search(names))


def _strings(out: dict, keys: Iterable[str]) -> list:
    return [key for key in keys if isinstance(out.get(key), str)]


def _typed(out: dict) -> None:
    """A typed value: credential fields redacted, anything else ID-masked."""
    redact = _secret_field(out) or _truthy(out.get("secret"))
    for key in _strings(out, ("text", "value")):
        out[key] = run_consts.SECRET_MASK if redact else mask_id(out[key])


def mask_node(out: dict) -> None:
    """Apply the three rules to one dict, in place (children are done already)."""
    if str(out.get("op") or "") in _TYPED_OPS:
        _typed(out)
    if _is_password_node(out):
        for key in _strings(out, ("text", "desc", "value")):
            out[key] = ""
    else:
        for key in _strings(out, ("text", "desc")):
            out[key] = mask_id(out[key])
    for key in _strings(out, [k for k in out if str(k).lower() in _PROSE_KEYS]):
        out[key] = mask_id(out[key])
