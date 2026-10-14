"""The one place a broad exception is swallowed: ``never_raise``.

Boundaries that must never raise (MCP handlers, background threads, cleanup
paths, "never block startup") wrap the risky block in ``with never_raise(...)``
instead of writing their own ``except Exception``. The fallback value goes in
the statement after the ``with``; ``guard.exc`` / ``guard.failed`` tell the
caller what happened. Logging keeps the caller's level and points at the
caller's line (``stacklevel=2``). Stdlib only: config/settings* may import
nothing else.
"""

from __future__ import annotations

import logging
from types import TracebackType
from typing import TYPE_CHECKING, Tuple, Type, TypedDict, Union

if TYPE_CHECKING:
    from typing_extensions import Unpack

Catch = Union[Type[BaseException], Tuple[Type[BaseException], ...]]


class GuardOptions(TypedDict, total=False):
    """How ``never_raise`` logs and what it catches (all optional)."""

    level: int
    exc_info: bool
    with_exc: bool
    catch: Catch


class never_raise:
    """Swallow ``catch`` (default ``Exception``) and log it at the caller's level.

    ``log=None`` swallows silently. ``with_exc`` appends the exception to the
    message args; ``exc_info`` attaches the traceback. ``KeyboardInterrupt``,
    ``SystemExit`` and ``asyncio.CancelledError`` pass through by default.
    """

    def __init__(
        self,
        log: logging.Logger | None = None,
        msg: str = "",
        *args: object,
        **options: Unpack[GuardOptions],
    ) -> None:
        self._log, self._msg, self._args = log, msg, args
        self._options = options
        self.exc: BaseException | None = None

    @property
    def failed(self) -> bool:
        """True when the guarded block raised and was swallowed."""
        return self.exc is not None

    def __enter__(self) -> never_raise:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        if exc is None or not isinstance(exc, self._options.get("catch", Exception)):
            return False
        self.exc = exc
        if self._log is not None:
            self._emit(self._log, exc)
        return True

    def _emit(self, log: logging.Logger, exc: BaseException) -> None:
        opts = self._options
        args = (*self._args, exc) if opts.get("with_exc") else self._args
        info = exc if opts.get("exc_info") else None
        level = opts.get("level", logging.DEBUG)
        log.log(level, self._msg, *args, exc_info=info, stacklevel=3)
