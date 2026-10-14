"""Thin private-name aliases for the public seams of the handler facades.

A facade exposes each patchable collaborator under a PUBLIC name (``x``). Old
callers and pinned tests still reach it as ``_x``; ``alias`` builds that private
name as a delegating function that resolves ``module.x`` at call time, so a
patch on either name takes effect. WX removes the aliases with their last user.
The forwarder copies the target's signature and async-ness.
"""

from __future__ import annotations

import inspect
from types import ModuleType
from typing import Any, Callable


def _pinned(forward: Callable[..., Any], public: str, like: Any) -> Callable[..., Any]:
    """Name the forwarder ``_<public>`` and give it the target's signature."""
    forward.__name__ = "_" + public
    if callable(like):
        forward.__signature__ = inspect.signature(like)  # type: ignore[attr-defined]
    return forward


def _forwarder(resolve: Callable[[], Any], public: str, like: Any):
    if inspect.iscoroutinefunction(like):

        async def _forward_async(*args: Any, **kwargs: Any) -> Any:
            return await getattr(resolve(), public)(*args, **kwargs)

        return _pinned(_forward_async, public, like)

    def _forward(*args: Any, **kwargs: Any) -> Any:
        return getattr(resolve(), public)(*args, **kwargs)

    return _pinned(_forward, public, like)


def alias_via(resolve: Callable[[], Any], public: str, like: Any) -> Callable[..., Any]:
    """Like ``alias`` for a module only reachable lazily (``resolve()`` at call time).

    ``like`` is the target as defined now; it fixes the signature and async-ness.
    """
    return _forwarder(resolve, public, like)


def alias(module: ModuleType, public: str) -> Callable[..., Any]:
    """Function that forwards to ``module.<public>`` looked up at call time."""
    return _forwarder(lambda: module, public, getattr(module, public, None))
