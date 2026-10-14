"""Async error boundary: one decorator for the 'log, capture, reply' catch block."""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

logger = logging.getLogger("tools.mcp_handlers")

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])
Reply = Callable[[BaseException], Any]
Hook = Callable[[BaseException], None]


def text_reply(prefix: str) -> Reply:
    """Reply builder producing ``f"{prefix}{exc}"``."""
    return lambda exc: f"{prefix}{exc}"


def error_dict(exc: BaseException) -> dict[str, Any]:
    """Reply builder for the never-raises dict contract."""
    return {"error": str(exc), "content": None}


class Boundary:
    """Decorator object holding the reply, log label and capture hook."""

    def __init__(self, reply: Reply, label: str, hook: Hook | None) -> None:
        self._reply = reply
        self._label = label
        self._hook = hook

    def __call__(self, fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:
                if self._label:
                    logger.exception(self._label)
                return self._failed(exc)

        return cast(F, wrapper)

    def _failed(self, exc: BaseException) -> Any:
        if self._hook is not None:
            self._hook(exc)
        return self._reply(exc)


def guarded(reply: Reply, label: str = "", hook: Hook | None = None) -> Boundary:
    """On Exception: log ``label`` (if set), run ``hook``, return ``reply(exc)``."""
    return Boundary(reply, label, hook)
