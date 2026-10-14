"""Cleaning the ``when`` half of a knowledge note."""

from __future__ import annotations

from typing import Any

from tools.mobile import appk_consts


def _clean(value: Any, label: str) -> tuple:
    """``(text, error)`` for one short string field."""
    if not isinstance(value, str):
        return "", "%s must be a string" % label
    text = " ".join(value.split())
    if len(text) > appk_consts._FIELD_CHARS:
        return "", "%s is over %d characters" % (label, appk_consts._FIELD_CHARS)
    return text, ""


def _env_ok(env: Any) -> bool:
    ok = isinstance(env, dict) and len(env) <= appk_consts._ENV_ENTRIES
    return ok and all(
        isinstance(k, str)
        and isinstance(v, str)
        and len(k) <= appk_consts._FIELD_CHARS
        and len(v) <= appk_consts._FIELD_CHARS
        for k, v in env.items()
    )


def _text_keys(when: dict) -> tuple:
    clean: dict = {}
    for key in ("op", "rid", "activity"):
        if key in when:
            value, problem = _clean(when[key], "when." + key)
            if problem:
                return {}, problem
            if value:
                clean[key] = value
    return clean, ""


def _validate_when(when: dict) -> tuple:
    unknown = sorted(str(key) for key in when if key not in appk_consts._WHEN_KEYS)
    if unknown:
        return {}, "unknown when key(s): " + ", ".join(unknown)
    clean, problem = _text_keys(when)
    if problem:
        return {}, problem
    if "env" in when:
        if not _env_ok(when["env"]):
            return {}, "when.env must map names to strings"
        if when["env"]:
            clean["env"] = dict(when["env"])
    op = clean.get("op")
    if op is not None and (not appk_consts._OP_RE.match(op) or op == "wait"):
        return {}, "when.op must be a lowercase op name other than wait"
    return clean, ""
