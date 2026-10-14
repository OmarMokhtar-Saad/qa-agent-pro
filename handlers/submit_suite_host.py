"""Registry through which the submit_suite_* leaves read the facade's patchable names.

Tests patch ``handlers.submit_suite.<name>``; a leaf that imported the name directly
would never see the patch. Leaves therefore call ``host().<name>`` at call time
(never at module level). The facade binds itself as the LAST statement of
``handlers/submit_suite.py``.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

_HOLDER: dict[str, ModuleType] = {}


def bind(module: ModuleType) -> None:
    """Record *module* (the ``handlers.submit_suite`` facade) as the host."""
    _HOLDER["host"] = module


def host() -> Any:
    """The bound facade module; fails loudly if a leaf runs before it exists."""
    try:
        return _HOLDER["host"]
    except KeyError:
        raise RuntimeError(
            "handlers.submit_suite_host is unbound: import handlers.submit_suite "
            "(the facade) before calling a submit_suite_* leaf."
        ) from None
