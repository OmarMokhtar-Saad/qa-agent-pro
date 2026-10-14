"""Registry through which the downloader_* leaves read the facade's patchable names.

Tests patch ``tools.mobile.downloader.<name>``; a leaf that imported the name directly
would never see the patch. Leaves therefore call ``host().<name>`` at call time.
The facade binds itself as the LAST statement of ``tools/mobile/downloader.py``.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

_HOLDER: dict[str, ModuleType] = {}


def bind(module: ModuleType) -> None:
    """Record *module* (the ``tools.mobile.downloader`` facade) as the host."""
    _HOLDER["host"] = module


def host() -> Any:
    """The bound facade module; fails loudly if a leaf runs before it exists."""
    try:
        return _HOLDER["host"]
    except KeyError:
        raise RuntimeError(
            "tools.mobile.downloader_host is unbound: import tools.mobile.downloader "
            "(the facade) before calling an downloader_* leaf."
        ) from None
