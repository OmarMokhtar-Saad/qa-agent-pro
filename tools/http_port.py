"""HTTP port: plain httpx calls behind module functions.

Call through the module (``http_port.get(...)``). Each function delegates at
call time to the httpx module attribute, so a test that patches ``httpx.get`` /
``httpx.post`` keeps intercepting. Plain clients only: SSRF-pinned fetching
stays in ``tools/net_guard.py`` and ``jira_fetcher``; do not route those here.
Timeouts are parameters, never module constants.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx


def get(url: str, headers: Mapping[str, str], timeout: float) -> httpx.Response:
    """GET following redirects."""
    return httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)


def post_json(url: str, payload: Mapping[str, Any], timeout: float) -> httpx.Response:
    """POST ``payload`` as JSON with an explicit content-type header."""
    return httpx.post(
        url,
        json=payload,
        timeout=timeout,
        headers={"Content-Type": "application/json"},
    )


def async_client(timeout: float) -> httpx.AsyncClient:
    """A plain async client with the given timeout."""
    return httpx.AsyncClient(timeout=timeout)
