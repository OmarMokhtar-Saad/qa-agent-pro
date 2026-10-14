"""Registry through which the generate_* leaves read the facade's patchable names.

Tests patch ``handlers.generate.<name>``; a leaf that imported the name directly
would never see the patch. Leaves therefore call ``host().<name>`` at call time
(never at module level). The facade binds itself as the LAST statement of
``handlers/generate.py``.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

_HOLDER: dict[str, ModuleType] = {}


def bind(module: ModuleType) -> None:
    """Record *module* (the ``handlers.generate`` facade) as the host."""
    _HOLDER["host"] = module


def host() -> Any:
    """The bound facade module; fails loudly if a leaf runs before it exists."""
    try:
        return _HOLDER["host"]
    except KeyError:
        raise RuntimeError(
            "handlers.generate_host is unbound: import handlers.generate "
            "(the facade) before calling a generate_* leaf."
        ) from None
