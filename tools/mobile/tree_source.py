"""Tree sources: where a screen's UI tree comes from.

One interface so the executor never names a mechanism. ``uiautomator dump`` is
the fallback every device supports (registered by ``dump_tree_source``); a
persistent on-device server (registered by ``u2_tree_source``) is faster on
slow devices but installs helper APKs, so it is selected only when the
``allowed`` predicate says THIS serial opted in. Selection never guesses:
without consent the fallback is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol

#: The registry name of the always-allowed ``uiautomator dump`` source.
FALLBACK_SOURCE = "uiautomator-dump"


@dataclass(frozen=True)
class TreeResult:
    """One dump: the hierarchy XML, how long it took, and who made it."""

    xml: str
    elapsed_ms: int
    source: str


class TreeSource(Protocol):
    name: str

    async def available(self, serial: str) -> bool:
        """True when this source can serve ``serial`` right now."""
        ...

    async def dump(self, serial: str, timeout_s: float) -> Optional[TreeResult]:
        """The current tree, or None when it could not be read in time."""
        ...


#: name -> (priority, source); lower priority is tried first.
_REGISTRY: dict = {}


def register_tree_source(source: TreeSource, priority: int = 100) -> None:
    _REGISTRY[source.name] = (priority, source)


def unregister_tree_source(name: str) -> None:
    _REGISTRY.pop(name, None)


def tree_source_names() -> list:
    return [src.name for _prio, src in sorted(_REGISTRY.values(), key=lambda p: p[0])]


def fallback_only(name: str, serial: str) -> bool:
    """The default consent predicate: nothing but the fallback is allowed."""
    return name == FALLBACK_SOURCE


async def select_tree_source(
    serial: str,
    allowed: Callable[[str, str], bool] = fallback_only,
) -> Optional[TreeSource]:
    """First registered source that is allowed for ``serial`` and available."""
    for _prio, source in sorted(_REGISTRY.values(), key=lambda p: p[0]):
        if allowed(source.name, serial) and await source.available(serial):
            return source
    return None
