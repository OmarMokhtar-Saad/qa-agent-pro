"""Late-bound view of the ``handlers.mobile.run`` facade for the mobile_run_* siblings.

Why it exists: tests patch names on ``handlers.mobile.run`` (the facade), and a
sibling must see that patch at call time. It imports nothing from handlers (no
static edge to the facade, so no cycle). ``mobile_run.<name>`` is untyped by design.
"""

from __future__ import annotations

import sys
from typing import Any


class _Facade:
    def __getattr__(self, name: str) -> Any:
        return getattr(sys.modules["handlers.mobile.run"], name)


mobile_run = _Facade()
