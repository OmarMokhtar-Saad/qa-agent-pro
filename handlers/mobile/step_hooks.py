"""Late-bound view of the ``handlers.mobile.step`` facade for the mobile_step_* siblings.

Why it exists: tests patch names on ``handlers.mobile.step`` (the facade), and a
sibling must see that patch at call time. It imports nothing from handlers (no
static edge to the facade, so no cycle). ``mobile_step.<name>`` is untyped by design.
"""

from __future__ import annotations

import sys
from typing import Any


class _Facade:
    def __getattr__(self, name: str) -> Any:
        return getattr(sys.modules["handlers.mobile.step"], name)


mobile_step = _Facade()
