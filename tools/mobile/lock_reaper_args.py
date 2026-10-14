"""Argument objects and the port protocol for :mod:`tools.mobile.lock_reaper`.

Kept apart so ``lock_reaper`` stays under the file-size limit; this module
imports nothing from ``tools.mobile`` at run time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional, Protocol

if TYPE_CHECKING:
    from tools.mobile import run_verdict


class ReleasePorts(Protocol):
    """The side effects; each may be a plain function or a coroutine function."""

    def stop_heartbeat(self, run_id: str) -> Any: ...

    def finalize(self, run_id: str, verdict: run_verdict.Verdict) -> Any: ...

    def release_lock(self, serial: str, run_id: str) -> Any: ...


@dataclass(frozen=True)
class IdleQuery:
    """Optional inputs of ``is_idle``; ``timeout_s=None`` means the module default."""

    persisted_ts: Optional[float] = None
    now: Optional[float] = None
    timeout_s: Optional[float] = None


@dataclass(frozen=True)
class ReleaseCtx:
    """Where and with what ports ``release_if_idle`` frees a device."""

    serial: str
    ports: Any
    persisted_ts: Optional[float] = None
    now: Optional[float] = None
