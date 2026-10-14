"""Parsing of the optional ``env`` call parameter (``name=value,name=value``)."""

from __future__ import annotations

import re

from tools.mobile import app_knowledge, knowledge_limits

_NAME_OK = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
MAX_ENV_VALUE = 200


def _entry(part: str, out: dict) -> str:
    """Add one ``name=value`` part to *out*; the refusal text, or ``""``."""
    name, sep, value = part.partition("=")
    name, value = name.strip(), value.strip()
    if not sep or not _NAME_OK.match(name):
        return "env entries look like name=value (names use A-Za-z0-9_.-)"
    if len(value) > MAX_ENV_VALUE:
        return "env value for %s is longer than %d" % (name, MAX_ENV_VALUE)
    if name in out and out[name] != value:
        return "env names %s twice" % name
    out[name] = value
    return ""


def _final(out: dict, raw: str) -> str:
    if len(out) > knowledge_limits.ENV_ENTRIES:
        return "env has more than %d entries" % knowledge_limits.ENV_ENTRIES
    if out and app_knowledge.secret_reason(raw):
        return "env looks like it holds a secret; it was not stored"
    return ""


def parse_env_checked(text: object) -> tuple[dict, str]:
    """``(env, error)``. An error means the text was refused and env is ``{}``."""
    raw = str(text or "").strip()
    if not raw:
        return {}, ""
    out: dict = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        error = _entry(part, out)
        if error:
            return {}, error
    error = _final(out, raw)
    return ({}, error) if error else (out, "")


def parse_env(text: object) -> dict:
    """The env named by *text*; ``{}`` when empty or refused."""
    return parse_env_checked(text)[0]
