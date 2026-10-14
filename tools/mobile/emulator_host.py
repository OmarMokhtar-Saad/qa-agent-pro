"""Registry through which the emulator_* leaves read the facade's patchable names.

Tests patch ``tools.mobile.emulator.<name>``; a leaf that imported the name directly
would never see the patch. Leaves therefore call ``host().<name>`` at call time.
The facade binds itself as the LAST statement of ``tools/mobile/emulator.py``.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

_HOLDER: dict[str, ModuleType] = {}


def bind(module: ModuleType) -> None:
    """Record *module* (the ``tools.mobile.emulator`` facade) as the host."""
    _HOLDER["host"] = module


def host() -> Any:
    """The bound facade module; fails loudly if a leaf runs before it exists."""
    try:
        return _HOLDER["host"]
    except KeyError:
        raise RuntimeError(
            "tools.mobile.emulator_host is unbound: import tools.mobile.emulator "
            "(the facade) before calling an emulator_* leaf."
        ) from None
