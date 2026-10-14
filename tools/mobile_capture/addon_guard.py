"""Swallow-and-report guard for ``addon.py`` (stdlib only, no tree imports).

``addon.py`` runs inside mitmdump's interpreter and cannot import
``config.settings_guard``; this is its ``never_raise`` twin: same
``__exit__``-based semantics (no except clause, ``Exception`` swallowed,
``KeyboardInterrupt``/``SystemExit`` pass through), but it reports to stderr
because that is the only channel the proxy child has.
"""

from __future__ import annotations

import sys
from types import TracebackType


class swallow:
    """Swallow ``Exception`` in the block and write ``<label>: <exc>`` to stderr."""

    def __init__(self, label: str) -> None:
        self._label = label
        self.exc: BaseException | None = None

    @property
    def failed(self) -> bool:
        """True when the guarded block raised and was swallowed."""
        return self.exc is not None

    def __enter__(self) -> swallow:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        if exc is None or not isinstance(exc, Exception):
            return False
        self.exc = exc
        sys.stderr.write(self._label + ": " + str(exc) + "\n")
        return True
