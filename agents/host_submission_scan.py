"""Host mode: the linear single-pass scanner that finds the suite object in submitted text."""

from __future__ import annotations

import json

from agents.host_prep_serde import PrepParseError

_Brace = tuple[int, int]  # (depth, start) of the top-level object being scanned


def _string_scan_state(ch: str, escaped: bool) -> tuple[bool, bool]:
    """One character inside a JSON string: return ``(in_string, escaped)``."""
    if escaped:
        return True, False
    if ch == "\\":
        return True, True
    return ch != '"', False


def _brace_scan_step(raw: str, i: int, brace: _Brace) -> tuple[_Brace, str | None]:
    """Apply the brace at ``raw[i]``: return ``(brace, closed_span)``.

    ``closed_span`` is the top-level object just completed, else ``None``.
    """
    depth, start = brace
    if raw[i] == "{":
        return (depth + 1, i if depth == 0 else start), None
    if depth == 0:
        return brace, None
    depth -= 1
    if depth == 0 and start >= 0:
        return (0, -1), raw[start : i + 1]
    return (depth, start), None


# Yield each TOP-LEVEL balanced ``{...}`` object in ``raw``, string/escape
# aware, in a SINGLE forward pass -- every character is visited at most once,
# so total work is O(len(raw)).
#
# This deliberately does NOT reuse ``llm._balanced_json_spans``: that helper
# re-scans forward from EVERY ``{`` (and yields nested spans), which is O(n^2)
# on adversarial input -- a 4 MB unbalanced-brace blob would scan for days, and
# even 64 KB takes minutes -- and it is shared with the server-mode ask_json
# parser, so it must not change. Host-submitted JSON is UNTRUSTED, so a linear,
# self-contained scanner is used here. ``budget`` is a hard ceiling on the
# number of characters visited; exceeding it raises PrepParseError so a hostile
# blob is rejected FAST rather than hanging.
def bounded_json_spans(raw: str, *, budget: int):
    n = len(raw)
    brace: _Brace = (0, -1)
    in_string = False
    escaped = False
    i = 0
    while i < n:
        if i >= budget:
            raise PrepParseError("submitted JSON exceeded the scan budget")
        ch = raw[i]
        if in_string:
            in_string, escaped = _string_scan_state(ch, escaped)
        elif ch == '"':
            in_string = True
        elif ch in "{}":
            brace, span = _brace_scan_step(raw, i, brace)
            if span is not None:
                yield span
        i += 1


def _bounded_json_spans(raw: str, *, budget: int):
    """Pinned alias (host_submission re-export; WX retires): calls through."""
    return bounded_json_spans(raw, budget=budget)


# Last resort: the whole stripped string as one object.
def _whole_text_suite(stripped: str) -> dict | None:
    try:
        obj = json.loads(stripped)
    except (json.JSONDecodeError, ValueError, RecursionError):
        obj = None
    if isinstance(obj, dict) and "test_cases" in obj:
        return obj
    return None


# A hard ceiling equal to the input length: the single-pass scanner visits
# each character at most once, so this never trips for legitimate input; it
# is an explicit invariant guard, while the caller's size cap bounds the input.
def _largest_suite_object(stripped: str) -> dict | None:
    """The largest balanced JSON object carrying ``test_cases``, else None."""
    best: dict | None = None
    best_len = -1
    for span in bounded_json_spans(stripped, budget=len(stripped) + 1):
        try:
            obj = json.loads(span)
        except (json.JSONDecodeError, ValueError, RecursionError):
            continue
        if isinstance(obj, dict) and "test_cases" in obj and len(span) > best_len:
            best, best_len = obj, len(span)
    if best is None:
        best = _whole_text_suite(stripped)
    return best
