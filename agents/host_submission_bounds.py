"""Host mode: depth and size bounds on an untrusted submission."""

from __future__ import annotations

import json

from agents.host_prep_serde import PrepParseError
from tools import settings_port

# F16 (2026-09-02 audit): the deepest `{`/`[` nesting parse_host_suite will look
# at. json.loads is RECURSIVE, so a submission of 40,000 open brackets raised
# RecursionError -- NOT the PrepParseError this function's contract promises --
# and only mcp_handlers' outer generic `except Exception` kept the tool alive.
# A real suite is five levels deep (object -> test_cases -> case -> steps ->
# step), so 200 is two orders of magnitude of headroom: this is a GUARD against
# a hostile shape, not a schema constraint anything legitimate can reach.
_MAX_JSON_DEPTH = 200


# Deepest `{`/`[` nesting in *text*, ignoring brackets inside JSON strings.
#
# ONE O(n) pass and NO recursion -- which is the whole point: it runs BEFORE
# json.loads precisely so the recursive decoder is never handed input that
# would exhaust the interpreter stack. String-aware, so a case title full of
# brackets cannot be mistaken for nesting.
def _text_nesting_depth(text: str) -> int:
    depth = deepest = 0
    in_string = escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
            deepest = max(deepest, depth)
        elif ch in "]}":
            depth -= 1
    return deepest


# Deepest container nesting in *obj*, stopping once *limit* is exceeded.
#
# Iterative for the same reason as :func:`_text_nesting_depth`: the MCP tool
# signatures accept an already-parsed object for ``suite_json``, and handing a
# 30,000-deep dict to json.dumps (which the size cap below must do to measure
# it) raised RecursionError before any of this function's own error handling
# could run.
def _object_nesting_depth(obj, limit: int) -> int:
    deepest = 0
    stack = [(obj, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > deepest:
            deepest = depth
            if deepest > limit:
                return deepest
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, (list, tuple)):
            stack.extend((v, depth + 1) for v in node)
    return deepest


# 2026-08-03 (Fix 1): the MCP tool signatures now accept an OBJECT for
# suite_json, so this branch is reachable from a real submission for the
# first time. It MUST honour the same size cap as the string branch
# below -- otherwise widening the annotation would have silently removed
# the only bound on submission size, since the cap sits after this early
# return. Serialising to measure costs the same order of memory as the
# string path already does.
def _check_submitted_object(text_or_obj, enforce_size_cap: bool) -> None:
    """Depth and size checks for an already-parsed submitted object."""
    if _object_nesting_depth(text_or_obj, _MAX_JSON_DEPTH) > _MAX_JSON_DEPTH:
        raise PrepParseError(
            f"submitted JSON nests deeper than {_MAX_JSON_DEPTH} levels"
        )
    cap = int(getattr(settings_port.get(), "qa_prep_max_bytes", 0) or 0)
    if cap and enforce_size_cap:
        try:
            size = len(
                json.dumps(text_or_obj, ensure_ascii=False).encode("utf-8", "ignore")
            )
        except (TypeError, ValueError, RecursionError) as exc:
            raise PrepParseError(
                f"submitted object is not JSON-serialisable: {exc}"
            ) from exc
        if size > cap:
            raise PrepParseError(f"submitted JSON exceeds the {cap}-byte cap")


def _check_text_size_cap(text: str, enforce_size_cap: bool) -> None:
    """Raise PrepParseError when ``text`` exceeds ``qa_prep_max_bytes``."""
    max_bytes = int(getattr(settings_port.get(), "qa_prep_max_bytes", 0) or 0)
    if (
        enforce_size_cap
        and max_bytes
        and len(text.encode("utf-8", "ignore")) > max_bytes
    ):
        raise PrepParseError(f"submitted JSON exceeds the {max_bytes}-byte cap")
