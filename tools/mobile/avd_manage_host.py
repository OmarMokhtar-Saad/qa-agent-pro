"""Registry through which the avd_manage_* leaves read the facade's patchable names.

Tests patch ``tools.mobile.avd_manage.<name>``; a leaf that imported the name directly
would never see the patch. Leaves therefore call ``host().<name>`` at call time.
The facade binds itself as the LAST statement of ``tools/mobile/avd_manage.py``.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

_HOLDER: dict[str, ModuleType] = {}


def bind(module: ModuleType) -> None:
    """Record *module* (the ``tools.mobile.avd_manage`` facade) as the host."""
    _HOLDER["host"] = module


def host() -> Any:
    """The bound facade module; fails loudly if a leaf runs before it exists."""
    try:
        return _HOLDER["host"]
    except KeyError:
        raise RuntimeError(
            "tools.mobile.avd_manage_host is unbound: import tools.mobile.avd_manage "
            "(the facade) before calling an avd_manage_* leaf."
        ) from None
