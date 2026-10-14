"""Swagger / OpenAPI link ingestion (always on since 2026-08-13).

``QA_SWAGGER_ENABLED`` was DELETED on 2026-08-13 (flag-surface reduction,
batch 6) and the behaviour hardcoded ON -- the value the distribution ``.env``
template always shipped. ``handlers`` now reaches this module
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

import asyncio
import json  # noqa: F401
import logging
from typing import (
    Any,
    cast,  # noqa: F401
)
from urllib.parse import urlparse  # noqa: F401

import yaml  # noqa: F401

from config.settings_guard import never_raise
from tools.jira_fetcher import _follow_redirects_with_pinning
from tools.swf_lines import (  # noqa: F401
    _HTTP_METHODS,
    _MAX_ENDPOINTS,
    _endpoint_line,
    _endpoint_lines,
    _param_names,
)
from tools.swf_spec import (  # noqa: F401
    _SPEC_SUFFIXES,
    _URL_HINTS,
    looks_like_openapi_url,
)
from tools.swf_spec import (
    _parse_spec as parse_spec,
)
from tools.swf_summary import (  # noqa: F401
    _MAX_CHARS,
    _auth_lines,
    _servers_lines,
    summarize_openapi,
    summary_result,
)

logger = logging.getLogger("qa_agents.swagger_fetcher")

# Absolute backstop even for the uncapped (grounding) caller: nothing legitimate is
# this large (GitHub's spec is ~9 MB), and yaml.safe_load on more is a CPU/memory sink.
HARD_MAX_CHARS = 32_000_000

# A fetched spec is held IN MEMORY while the API agent parses it. OPT-IN:
# fetch_openapi_spec passes no cap, so the shipped test-case-grounding caller is
# unbounded exactly as before; only the API agent asks for this bound.
#
# WHAT THIS BOUNDS, and what it does not. The raw document is parsed once
# (agents/api_test_agent -> process_intake -> parse_document) and every
# endpoint the tester can pick is extracted and REDACTED from it while it is
# still only in memory; the picker state persisted to prep_store is built by
# _make_pending, which its own docstring says is "NEVER the raw source" and is
# bounded by _MAX_PENDING_BYTES. The raw spec therefore never reaches the
# store, and this cap bounds ONE thing: the text the parser and the extractor
# hold in memory for the duration of a single call.
#
# Two repairs of this number on 2026-09-06 sized it against prep_store --
# first at 4 bytes per character for ensure_ascii=False, then at 12 for
# prep_store's own escaping -- and cut it to 320_000. An independent review
# the same day measured a 150-operation pure-ASCII OpenAPI 3 document at
# 512_690 characters as served: REFUSED, with the tester told to paste one
# endpoint at a time, for a payload that is never persisted at all. A cap
# sized against a consumer that does not consume it is wrong at every value.
# Real public specs as served: Petstore ~30 KB, GitHub ~9 MB, Stripe ~6 MB,
# Kubernetes ~4 MB. The parser cost is roughly ten times the text in Python
# objects, transiently, in a single-tester stdio process; four million
# characters admits every mid-size internal API and refuses the pathological
# document by name, and is where this cap stood before it was mis-sized.
# tests/test_bounds_upper.py drives the fetcher at the shipped value with a
# realistic large spec (the floor) and drives the agent to prove the store
# receives the picker, not the spec (the relation); comparing this integer to
# the store's byte cap is the mistake both repairs made and is what the AST
# guard in tests/test_bounds_reasons.py refuses.
_MAX_SPEC_CHARS = 4_000_000


def _oversize_error(text: str, max_chars: int | None) -> str:
    """The refusal message when *text* breaks the caller's or the hard cap, else ''."""
    if max_chars is not None and text and len(text) > max_chars:
        return (
            f"spec document is larger than {max_chars} characters "
            "- paste the single endpoint instead"
        )
    if text and len(text) > HARD_MAX_CHARS:
        return (
            f"spec document is larger than {HARD_MAX_CHARS} characters"
            " - refusing to parse it"
        )
    return ""


def _refusal(hop: Any, max_chars: int | None) -> dict | None:
    """The error dict for an HTTP failure or an oversize body, else None."""
    if hop.status_code >= 400:
        return {"error": f"HTTP {hop.status_code} fetching OpenAPI spec", "spec": None}
    too_large = _oversize_error(hop.text, max_chars)
    return {"error": too_large, "spec": None} if too_large else None


async def follow_redirects_with_pinning(url: str) -> tuple[Any, str]:
    """Public seam for the pinned fetch; calls the private name at call time."""
    return await _follow_redirects_with_pinning(url)


# Fetch + parse an OpenAPI spec URL into the PARSED DICT. NEVER raises.
# Returns ``{"error": None, "url": <final url>, "spec": <dict>}`` on success,
# else ``{"error": <reason>, "spec": None}``.
#
# This is the module's SINGLE network call site: :func:`fetch_openapi_spec`
# (the prose-summary face used by the test-case grounding path) delegates to
# it, so there is exactly one fetch path and exactly one SSRF stack. That
# stack -- scheme/DNS/public-IP validation, IP pinning and manual per-hop
# redirect validation in ``tools.jira_fetcher`` -- is reached through the
# identical ``_follow_redirects_with_pinning`` call as before: not
# reimplemented, not parameterised, not bypassed.
#
# The API test agent needs the parsed dict, not the prose summary.
#
# ``max_chars`` is OPT-IN and defaults to no cap, so the long-standing
# :func:`fetch_openapi_spec` behaviour is byte-for-byte unchanged. Only the new
# API-agent caller passes a bound. Capping unconditionally here would silently
# impose a new refusal on the shipped test-case grounding path.
async def _document_of(url: str, max_chars: int | None) -> dict:
    hop, final_url = await follow_redirects_with_pinning(url)
    refusal = _refusal(hop, max_chars)
    if refusal:
        return refusal
    # json/yaml parsing of a multi-MB spec is CPU-bound: keep it off the loop.
    spec = await asyncio.to_thread(parse_spec, hop.text)
    if spec is None:
        return {
            "error": "URL did not return a parseable OpenAPI/Swagger document",
            "spec": None,
        }
    return {"error": None, "url": final_url, "spec": spec}


async def _summary_of(url: str) -> dict:
    # The summarising half must stay inside the module's "never raises to
    # callers" house rule: sorted() over mixed-type YAML path keys raises
    # TypeError, and the caller is the live test-case grounding path.
    fetched = await fetch_openapi_document(url)
    if fetched.get("error"):
        return {"error": fetched["error"], "summary": None}
    return summary_result(fetched)


async def fetch_openapi_document(url: str, *, max_chars: int | None = None) -> dict:
    with never_raise(
        logger,
        "OpenAPI spec fetch failed for %s: %s",
        url,
        level=logging.WARNING,
        with_exc=True,
    ) as g:
        return await _document_of(url, max_chars)
    return {"error": str(g.exc) or g.exc.__class__.__name__, "spec": None}


async def fetch_openapi_spec(url: str) -> dict:
    # Fetch + parse + summarize an OpenAPI spec URL. NEVER raises.
    #
    # Returns {"error": None, "url", "title", "version", "endpoint_count",
    # "summary"} on success, else {"error": <reason>, "summary": None}.
    #
    # The public return contract is unchanged; only the fetch+parse half moved
    # into fetch_openapi_document so both faces share one network path.
    # No cap is passed, so this face refuses nothing it did not refuse before.
    with never_raise(
        logger,
        "OpenAPI spec summarize failed for %s: %s",
        url,
        level=logging.WARNING,
        with_exc=True,
    ) as g:
        return await _summary_of(url)
    return {"error": str(g.exc) or g.exc.__class__.__name__, "summary": None}
