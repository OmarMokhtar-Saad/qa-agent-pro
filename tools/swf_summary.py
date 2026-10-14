"""Bounded plain-text summary of a parsed OpenAPI spec (leaf of ``tools.swagger_fetcher``)."""

from __future__ import annotations

from typing import cast

from tools.swf_lines import _HTTP_METHODS, _endpoint_lines

_MAX_CHARS = 12000


def summarize_openapi(spec: dict) -> str:
    """Condense a parsed spec into a bounded plain-text endpoint summary."""
    info = cast("dict", spec.get("info") if isinstance(spec.get("info"), dict) else {})
    lines = []
    title = str(info.get("title") or "Untitled API").strip()
    version = str(info.get("version") or "").strip()
    lines.append(f"API: {title}" + (f" (version {version})" if version else ""))
    desc = str(info.get("description") or "").strip()
    if desc:
        lines.append(desc[:500])
    lines.extend(_servers_lines(spec))
    lines.extend(_auth_lines(spec))
    lines.append("")
    lines.append("Endpoints (* = required param):")
    lines.extend(_endpoint_lines(spec))
    return "\n".join(lines)[:_MAX_CHARS]


def _servers_lines(spec: dict) -> list[str]:
    """The ``Servers:`` summary line (at most 5 urls), or []."""
    servers = spec.get("servers") or []
    if isinstance(servers, list) and servers:
        urls = [
            str(s.get("url"))
            for s in servers[:5]
            if isinstance(s, dict) and s.get("url")
        ]
        if urls:
            return ["Servers: " + ", ".join(urls)]
    return []


def _auth_lines(spec: dict) -> list[str]:
    """The ``Auth:`` summary line (at most 6 schemes), or []."""
    schemes = {}
    components = spec.get("components")
    if isinstance(components, dict) and isinstance(
        components.get("securitySchemes"), dict
    ):
        schemes = components["securitySchemes"]
    elif isinstance(spec.get("securityDefinitions"), dict):  # Swagger 2.0
        schemes = spec["securityDefinitions"]
    if schemes:
        auth = [
            f"{name} ({scheme.get('type', '?')})"
            for name, scheme in list(schemes.items())[:6]
            if isinstance(scheme, dict)
        ]
        if auth:
            return ["Auth: " + ", ".join(auth)]
    return []


def endpoint_count(spec: dict) -> int:
    """Number of dict-valued HTTP operations under ``paths``."""
    paths = spec.get("paths") or {}
    return sum(
        1
        for item in paths.values()
        if isinstance(item, dict)
        for m in _HTTP_METHODS
        if isinstance(item.get(m), dict)
    )


def summary_result(fetched: dict) -> dict:
    """The success dict of ``fetch_openapi_spec`` built from a fetched document."""
    spec = fetched["spec"]
    info = cast("dict", spec.get("info") if isinstance(spec.get("info"), dict) else {})
    return {
        "error": None,
        "url": fetched["url"],
        "title": str(info.get("title") or "Untitled API"),
        "version": str(info.get("version") or ""),
        "endpoint_count": endpoint_count(spec),
        "summary": summarize_openapi(spec),
    }
