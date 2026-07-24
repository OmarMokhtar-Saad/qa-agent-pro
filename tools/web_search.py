from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urlparse

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)

_DUCKDUCKGO_URL = "https://api.duckduckgo.com/"
_TIMEOUT = 10  # seconds


async def search_web(query: str) -> dict:
    """Search the web for a query using DuckDuckGo Instant Answer API.

    Returns:
        On success: {"error": None, "content": str, "sources": list[str]}
        On failure: {"error": str, "content": None}

    Never raises — all exceptions are caught and returned as error dicts.
    """
    try:
        if not settings.qa_web_search_enabled:
            return {"error": "Web search disabled", "content": None}

        if not query or not query.strip():
            return {"error": "Empty query", "content": None}

        query = query.strip()
        params = {
            "q": query,
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1",
        }

        # SSRF guard: resolve the DuckDuckGo API hostname before connecting
        hostname = urlparse(_DUCKDUCKGO_URL).hostname or ""
        if hostname:
            try:
                loop = asyncio.get_running_loop()
                infos = await loop.getaddrinfo(hostname, None)
                for info in infos:
                    addr = ipaddress.ip_address(info[4][0])
                    if not addr.is_global:
                        logger.error(
                            "SSRF guard: DuckDuckGo API hostname '%s' resolved to non-public address %s",
                            hostname,
                            addr,
                        )
                        return {
                            "error": "Blocked: search endpoint resolved to non-public address",
                            "content": None,
                        }
            except socket.gaierror as exc:
                logger.warning("DNS resolution failed for search endpoint: %s", exc)
                return {
                    "error": f"Blocked: could not resolve search endpoint hostname '{hostname}'",
                    "content": None,
                }

        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(
                _DUCKDUCKGO_URL,
                params=params,
                headers={"User-Agent": "Mozilla/5.0 QA-Agents/1.0"},
            )

        if resp.status_code >= 400:
            logger.warning("Web search HTTP %d for query: %s", resp.status_code, query)
            return {
                "error": f"HTTP {resp.status_code}: search request failed",
                "content": None,
            }

        try:
            data = resp.json()
        except Exception as exc:
            logger.warning("Web search response is not valid JSON: %s", exc)
            return {"error": "Invalid JSON response from search API", "content": None}

        return _parse_duckduckgo_response(data, query)

    except Exception as exc:
        logger.exception("Web search failed for query '%s'", query)
        return {"error": str(exc), "content": None}


def _parse_duckduckgo_response(data: dict, query: str) -> dict:
    """Extract useful text and source URLs from the DuckDuckGo Instant Answer JSON."""
    try:
        parts: list[str] = []
        sources: list[str] = []

        abstract = (data.get("Abstract") or "").strip()
        abstract_url = (data.get("AbstractURL") or "").strip()
        if abstract:
            parts.append(abstract)
        if abstract_url:
            sources.append(abstract_url)

        answer = (data.get("Answer") or "").strip()
        if answer:
            parts.append(answer)

        definition = (data.get("Definition") or "").strip()
        definition_url = (data.get("DefinitionURL") or "").strip()
        if definition:
            parts.append(definition)
        if definition_url and definition_url not in sources:
            sources.append(definition_url)

        for topic in (data.get("RelatedTopics") or [])[:5]:
            if not isinstance(topic, dict):
                continue
            text = (topic.get("Text") or "").strip()
            url = (topic.get("FirstURL") or "").strip()
            if text:
                parts.append(text)
            if url and url not in sources:
                sources.append(url)

        content = "\n\n".join(parts).strip()

        if not content:
            logger.info("Web search returned no usable content for query: %s", query)
            return {
                "error": "No usable content returned from search API",
                "content": None,
            }

        logger.info(
            "Web search succeeded for query '%s': %d chars, %d sources",
            query,
            len(content),
            len(sources),
        )
        return {"error": None, "content": content, "sources": sources}

    except Exception as exc:
        logger.exception("Error parsing DuckDuckGo response")
        return {"error": str(exc), "content": None}
