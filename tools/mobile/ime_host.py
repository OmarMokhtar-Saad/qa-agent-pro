"""Registry through which the ime_* leaves read the facade's patchable names.

Tests patch ``tools.mobile.ime.<name>``; a leaf that imported the name directly
would never see the patch. Leaves therefore call ``host().<name>`` at call time.
The facade binds itself right after its imports in ``tools/mobile/ime.py``.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

_HOLDER: dict[str, ModuleType] = {}


def bind(module: ModuleType) -> None:
    """Record *module* (the ``tools.mobile.ime`` facade) as the host."""
    _HOLDER["host"] = module


def host() -> Any:
    """The bound facade module; fails loudly if a leaf runs before it exists."""
    try:
        return _HOLDER["host"]
    except KeyError:
        raise RuntimeError(
            "tools.mobile.ime_host is unbound: import tools.mobile.ime "
            "(the facade) before calling an ime_* leaf."
        ) from None
