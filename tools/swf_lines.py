"""Per-endpoint summary lines for an OpenAPI spec (leaf of ``tools.swagger_fetcher``)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import NamedTuple

# Bounds keeping the grounding block prompt-sized even for huge specs.
_MAX_ENDPOINTS = 80
_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")


class _Op(NamedTuple):
    """One operation: its method, path, operation dict and enclosing path item."""

    method: str
    path: str
    op: dict
    path_item: dict


def _named_params(op: dict, path_item: dict) -> Iterator[dict]:
    """Parameter dicts with a name, path-level first, de-duplicated by (name, in)."""
    seen = set()
    for source in (path_item.get("parameters") or [], op.get("parameters") or []):
        if not isinstance(source, list):
            continue
        for p in source:
            if not isinstance(p, dict) or not p.get("name"):
                continue
            key = (p.get("name"), p.get("in"))
            if key in seen:
                continue
            seen.add(key)
            yield p


def _param_label(p: dict) -> str:
    """``name (in)`` with a trailing ``*`` for a required parameter."""
    label = str(p["name"])
    if p.get("in"):
        label += f" ({p['in']})"
    if p.get("required"):
        label += "*"
    return label


def _param_names(op: dict, path_item: dict) -> list:
    """Merged operation + path-level parameter names, required ones marked."""
    return [_param_label(p) for p in _named_params(op, path_item)]


def _endpoint_line(o: _Op) -> str:
    """One summary line for a single operation."""
    line = f"- {o.method.upper()} {o.path}"
    label = str(o.op.get("summary") or o.op.get("operationId") or "").strip()
    if label:
        line += f" — {label[:100]}"
    params = _param_names(o.op, o.path_item)
    if params:
        line += "; params: " + ", ".join(params[:8])
    body = o.op.get("requestBody")
    if isinstance(body, dict):
        line += "; has request body" + ("*" if body.get("required") else "")
    responses = o.op.get("responses")
    if isinstance(responses, dict) and responses:
        line += "; responses: " + ", ".join(sorted(str(c) for c in responses)[:8])
    return line


def _operations(spec: dict) -> Iterator[_Op]:
    """Every dict-valued HTTP operation under ``paths``, paths sorted."""
    for path in sorted(spec.get("paths") or {}):
        path_item = spec["paths"].get(path)
        if not isinstance(path_item, dict):
            continue
        for method in _HTTP_METHODS:
            op = path_item.get(method)
            if isinstance(op, dict):
                yield _Op(method=method, path=path, op=op, path_item=path_item)


def _endpoint_lines(spec: dict) -> list[str]:
    """Endpoint lines up to _MAX_ENDPOINTS, plus a truncation note."""
    lines: list[str] = []
    total = 0
    for o in _operations(spec):
        total += 1
        if total <= _MAX_ENDPOINTS:
            lines.append(_endpoint_line(o))
    if total > len(lines):
        lines.append(f"... and {total - len(lines)} more endpoints (truncated).")
    return lines
