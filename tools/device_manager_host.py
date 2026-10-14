"""Registry through which the device_manager_* leaves read the facade's patchable names.

Tests patch ``tools.device_manager.<name>``; a leaf that imported the name directly
would never see the patch. Leaves therefore call ``host().<name>`` at call time.
The facade binds itself as the LAST statement of ``tools/device_manager.py``.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

_HOLDER: dict[str, ModuleType] = {}


def bind(module: ModuleType) -> None:
    """Record *module* (the ``tools.device_manager`` facade) as the host."""
    _HOLDER["host"] = module


def host() -> Any:
    """The bound facade module; fails loudly if a leaf runs before it exists."""
    try:
        return _HOLDER["host"]
    except KeyError:
        raise RuntimeError(
            "tools.device_manager_host is unbound: import tools.device_manager "
            "(the facade) before calling a device_manager_* leaf."
        ) from None
