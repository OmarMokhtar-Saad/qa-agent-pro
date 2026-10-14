"""Audit port: stdlib-only slot between audit callers and the audit writer.

Call through the module: ``await audit_port.emit(event)``. ``emit`` forwards
to the bound writer at call time, so a test swaps the writer with ``use(fake)``
and production code never names the writer module. ``tools.audit_log`` binds
its writer on import; until something binds, ``emit`` is a no-op that reports
success. This module imports nothing internal (enforced by import-linter).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any

Writer = Callable[[Any], Awaitable[dict]]


async def _noop(event: Any) -> dict:
    return {"error": None, "content": None}


class _Slot:
    """Holds the currently bound writer."""

    writer: Writer = _noop


_SLOT = _Slot()


def bind(writer: Writer) -> None:
    """Install ``writer`` as the audit writer (composition root only)."""
    _SLOT.writer = writer


async def emit(event: Any) -> dict:
    """Send ``event`` to the bound writer and return its result dict."""
    return await _SLOT.writer(event)


@contextmanager
def use(fake: Writer) -> Iterator[None]:
    """Replace the writer inside the block, then restore the previous one."""
    previous = _SLOT.writer
    _SLOT.writer = fake
    try:
        yield
    finally:
        _SLOT.writer = previous
