"""Reply shapes of the per-app knowledge store: ``{"error", "content"}``."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("tools.mobile.app_knowledge")


def _ok(content: Any) -> dict:
    return {"error": None, "content": content}


def _err(message: str) -> dict:
    return {"error": message, "content": None}


def _fail(what: str, exc: BaseException | None) -> dict:
    logger.warning("app_knowledge.%s failed: %s", what, exc, exc_info=exc)
    return _err("the app knowledge store is unavailable (%s)" % type(exc).__name__)
