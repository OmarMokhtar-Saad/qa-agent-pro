"""Swagger / OpenAPI link ingestion (always on since 2026-08-13).

``QA_SWAGGER_ENABLED`` was DELETED on 2026-08-13 (flag-surface reduction,
batch 6) and the behaviour hardcoded ON -- the value the distribution ``.env``
template always shipped. ``tools.mcp_handlers`` now reaches this module
whenever :func:`looks_like_openapi_url` recognises a pasted URL.

Fetches an OpenAPI (Swagger) specification from a pasted URL and condenses it
into a bounded, human-readable endpoint summary used to ground API test-case
generation.

House rules honored:
- **Never raises to callers** — the public fetch returns
  ``{"error": <str>, "summary": None}`` on any failure, mirroring
  ``tools/jira_fetcher.py``.
- Reuses jira_fetcher's SSRF hardening (scheme/DNS/public-IP validation +
  IP-pinned, manually-validated redirects) — no new network primitives.
- The summary is externally-sourced text: callers MUST wrap it via
  ``tools.untrusted.wrap_untrusted`` before it reaches the LLM (the test
  scenario agent wraps it as ``openapi_spec``).
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

import yaml

from tools.jira_fetcher import _follow_redirects_with_pinning

logger = logging.getLogger("qa_agents.swagger_fetcher")

# Bounds keeping the grounding block prompt-sized even for huge specs.
_MAX_ENDPOINTS = 80
_MAX_CHARS = 12000
_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")

# URL substrings that mark a likely OpenAPI/Swagger document.
_URL_HINTS = ("swagger", "openapi", "api-docs", "api_docs")
_SPEC_SUFFIXES = (".json", ".yaml", ".yml")


def looks_like_openapi_url(url: str) -> bool:
    """Cheap pre-filter: does this URL plausibly point at an OpenAPI spec?

    Used by app.py to decide whether to try the spec fetch before the generic
    HTML page fetch. False negatives just fall back to the page path; false
    positives are caught by the parse step (which then falls back too).
    """
    try:
        parsed = urlparse((url or "").strip().lower())
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    haystack = parsed.path + "?" + parsed.query
    if any(hint in haystack for hint in _URL_HINTS):
        return True
    return parsed.path.endswith(_SPEC_SUFFIXES)


def _parse_spec(text: str) -> dict | None:
    """Parse JSON-or-YAML text into an OpenAPI/Swagger dict, else ``None``.

    Accepts only mappings carrying an ``openapi``/``swagger`` version marker
    and a ``paths`` mapping — anything else is "not a spec", never an error.
    """
    if not text or not text.strip():
        return None
    data = None
    try:
        data = json.loads(text)
    except ValueError:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            return None
    if not isinstance(data, dict):
        return None
    if "openapi" not in data and "swagger" not in data:
        return None
    if not isinstance(data.get("paths"), dict):
        return None
    return data


def _param_names(op: dict, path_item: dict) -> list:
    """Merged operation + path-level parameter names, required ones marked."""
    names = []
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
            label = str(p["name"])
            if p.get("in"):
                label += f" ({p['in']})"
            if p.get("required"):
                label += "*"
            names.append(label)
    return names


def summarize_openapi(spec: dict) -> str:
    """Condense a parsed spec into a bounded plain-text endpoint summary."""
    info = spec.get("info") if isinstance(spec.get("info"), dict) else {}
    lines = []
    title = str(info.get("title") or "Untitled API").strip()
    version = str(info.get("version") or "").strip()
    lines.append(f"API: {title}" + (f" (version {version})" if version else ""))
    desc = str(info.get("description") or "").strip()
    if desc:
        lines.append(desc[:500])
    servers = spec.get("servers") or []
    if isinstance(servers, list) and servers:
        urls = [
            str(s.get("url"))
            for s in servers[:5]
            if isinstance(s, dict) and s.get("url")
        ]
        if urls:
            lines.append("Servers: " + ", ".join(urls))
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
            lines.append("Auth: " + ", ".join(auth))
    lines.append("")
    lines.append("Endpoints (* = required param):")
    shown = 0
    total = 0
    for path in sorted(spec.get("paths") or {}):
        path_item = spec["paths"].get(path)
        if not isinstance(path_item, dict):
            continue
        for method in _HTTP_METHODS:
            op = path_item.get(method)
            if not isinstance(op, dict):
                continue
            total += 1
            if shown >= _MAX_ENDPOINTS:
                continue
            shown += 1
            line = f"- {method.upper()} {path}"
            label = str(op.get("summary") or op.get("operationId") or "").strip()
            if label:
                line += f" — {label[:100]}"
            params = _param_names(op, path_item)
            if params:
                line += "; params: " + ", ".join(params[:8])
            body = op.get("requestBody")
            if isinstance(body, dict):
                line += "; has request body" + ("*" if body.get("required") else "")
            responses = op.get("responses")
            if isinstance(responses, dict) and responses:
                line += "; responses: " + ", ".join(
                    sorted(str(c) for c in responses)[:8]
                )
            lines.append(line)
    if total > shown:
        lines.append(f"... and {total - shown} more endpoints (truncated).")
    return "\n".join(lines)[:_MAX_CHARS]


async def fetch_openapi_spec(url: str) -> dict:
    """Fetch + parse + summarize an OpenAPI spec URL. NEVER raises.

    Returns ``{"error": None, "url", "title", "version", "endpoint_count",
    "summary"}`` on success, else ``{"error": <reason>, "summary": None}``.
    """
    try:
        hop, final_url = await _follow_redirects_with_pinning(url)
        if hop.status_code >= 400:
            return {
                "error": f"HTTP {hop.status_code} fetching OpenAPI spec",
                "summary": None,
            }
        spec = _parse_spec(hop.text)
        if spec is None:
            return {
                "error": "URL did not return a parseable OpenAPI/Swagger document",
                "summary": None,
            }
        info = spec.get("info") if isinstance(spec.get("info"), dict) else {}
        paths = spec.get("paths") or {}
        endpoint_count = sum(
            1
            for item in paths.values()
            if isinstance(item, dict)
            for m in _HTTP_METHODS
            if isinstance(item.get(m), dict)
        )
        return {
            "error": None,
            "url": final_url,
            "title": str(info.get("title") or "Untitled API"),
            "version": str(info.get("version") or ""),
            "endpoint_count": endpoint_count,
            "summary": summarize_openapi(spec),
        }
    except Exception as exc:
        logger.warning("OpenAPI spec fetch failed for %s: %s", url, exc)
        return {"error": str(exc) or exc.__class__.__name__, "summary": None}
