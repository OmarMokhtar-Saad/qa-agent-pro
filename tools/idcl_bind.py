"""Binding extraction leaf of the identifier-collision detector."""

from __future__ import annotations

import re

from tools.idcl_text import (
    _GENERIC,
    _LABEL_MAX_TOKENS,
    _TOKEN_RE,
    _drop,
    _fold,
    _safe_label,
)

_MIN_BINDING_TOKENS = 2

# The emphasis class is load-bearing, and it was found by running this detector
# over the REAL TICKET-5646 ticket rather than over a fixture: it returned [] on
# the very collision it was written for. That ticket writes its screen table as
# `| **DF03** | Return bottom sheet: ... |`, and requiring the delimiter to
# follow the id IMMEDIATELY meant the `**` suppressed the definitional binding
# -- so guard 1, which demands one, could never fire and EVERY table definition
# in the ticket was invisible. Bold/italic/code around an id is the normal
# rendering of a Jira or markdown spec table, not an edge case. Bounded to 3
# characters so this stays a delimiter tolerance and not a general scan.
_DEFINITIONAL_RE = re.compile(r"^[*_~`]{0,3}[ \t]*[=|][ \t]*(.*)$")
_TERMINATOR_RE = re.compile(r"[.;|]")
_BEFORE_CUT_RE = re.compile(r"[.;|:]")


def _definition_tokens(tail: str) -> list:
    out = []
    for t in _TOKEN_RE.findall(tail)[:_LABEL_MAX_TOKENS]:
        if t.lower() in _GENERIC or _drop(t):
            continue
        out.append(t)
    return out


def _usage_tokens(before: str) -> list:
    out: list = []
    for t in reversed(_TOKEN_RE.findall(before)):
        if len(out) >= _LABEL_MAX_TOKENS:
            break
        if t.lower() in _GENERIC:
            continue
        if _drop(t):
            break
        out.append(t)
    out.reverse()
    return out


def _definition_binding(tail_of_line: str) -> list:
    out: list = []
    m = _DEFINITIONAL_RE.match(tail_of_line)
    if m:
        tail = m.group(1)
        cut = _TERMINATOR_RE.search(tail)
        if cut:
            tail = tail[: cut.start()]
        toks = _definition_tokens(tail)
        if len(toks) >= _MIN_BINDING_TOKENS:
            out.append(("definition", _safe_label(tail), _fold(toks)))
    return out


def _usage_binding(before: str) -> list:
    out: list = []
    cut2 = None
    for m2 in _BEFORE_CUT_RE.finditer(before):
        cut2 = m2.end()
    if cut2 is not None:
        before = before[cut2:]
    toks_u = _usage_tokens(before)
    if len(toks_u) >= _MIN_BINDING_TOKENS:
        out.append(("usage", _safe_label(" ".join(toks_u)), _fold(toks_u)))
    return out


def bindings_in_line(line: str, start: int, end: int) -> list:
    return _definition_binding(line[end:]) + _usage_binding(line[:start])
