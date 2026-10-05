"""Answers remembered for a run, keyed by device serial and the tester's text.

The resolved-app cache used to be keyed by the app text alone, so a package
chosen on one device was replayed on another. The key now carries the serial.
Only an answer the tester GAVE is stored -- an ambiguous or empty case is never
cached -- and an entry whose package is no longer installed is dropped.
"""

from __future__ import annotations

from typing import Iterable, Optional

#: Answers kept per process; the oldest is dropped first. A run asks a handful
#: of questions, so this is a runaway guard, not a working set.
MAX_RUN_ANSWERS = 32


def cache_key(serial: str, text: str) -> str:
    """``<serial>|<normalised text>``: stable, lower-cased, no secrets."""
    words = " ".join(str(text or "").lower().split())
    return (str(serial or "").strip() + "|" + words)[:200]


class RunAnswers:
    """Bounded ``(serial, text) -> package`` memory of the tester's own answers."""

    def __init__(self) -> None:
        self._items: dict = {}

    def get(self, serial: str, text: str) -> Optional[str]:
        return self._items.get(cache_key(serial, text))

    def remember(self, serial: str, text: str, value: Optional[str]) -> None:
        key = cache_key(serial, text)
        if not value or key.endswith("|"):
            return
        self._items.pop(key, None)
        self._items[key] = str(value)
        while len(self._items) > MAX_RUN_ANSWERS:
            del self._items[next(iter(self._items))]

    def forget(self, serial: str, text: str) -> None:
        self._items.pop(cache_key(serial, text), None)

    def drop_missing(self, serial: str, installed: Iterable[str]) -> None:
        """Drop this serial's answers whose package is no longer installed."""
        present = set(installed)
        prefix = str(serial or "").strip() + "|"
        stale = [
            key
            for key, value in self._items.items()
            if key.startswith(prefix) and value not in present
        ]
        for key in stale:
            del self._items[key]
