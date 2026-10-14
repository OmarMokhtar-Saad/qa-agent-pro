"""Late-bound view of the ``handlers.export`` facade for the export_* siblings.

Why it exists: tests patch names on ``handlers.export`` (the facade), and a
sibling must see that patch at call time. It imports nothing from handlers (no
static edge to the facade, so no cycle). ``export.<name>`` is untyped by design.
"""

from __future__ import annotations

import sys
from typing import Any


class _Facade:
    def __getattr__(self, name: str) -> Any:
        return getattr(sys.modules["handlers.export"], name)


export = _Facade()
