"""Handler-layer error boundary: log, ``capture_error``, reply."""

from __future__ import annotations

from handlers import _common
from tools.error_boundary import Boundary, Hook, Reply, guarded


def _capture(tool: str) -> Hook:
    return lambda exc: _common.capture_error(exc, tool)


def handler_guard(tool: str, label: str, reply: Reply) -> Boundary:
    """Decorate an async handler body with the standard failure boundary."""
    return guarded(reply, label, _capture(tool))
