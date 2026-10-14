"""Registry through which the _elicit_* leaves read the facade's patchable names.

Tests patch ``handlers._elicit.<name>``; a leaf that imported the name directly
would never see the patch. Leaves therefore call ``host().<name>`` at call time.
The facade binds itself as the LAST statement of ``handlers/_elicit.py``.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

_HOLDER: dict[str, ModuleType] = {}


def bind(module: ModuleType) -> None:
    """Record *module* (the ``handlers._elicit`` facade) as the host."""
    _HOLDER["host"] = module


def host() -> Any:
    """The bound facade module; fails loudly if a leaf runs before it exists."""
    try:
        return _HOLDER["host"]
    except KeyError:
        raise RuntimeError(
            "handlers._elicit_host is unbound: import handlers._elicit "
            "(the facade) before calling an _elicit_* leaf."
        ) from None
