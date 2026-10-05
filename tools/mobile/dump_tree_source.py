"""The ``uiautomator dump`` tree source: the fallback every device supports.

It wraps the executor's guarded dump (``executor.dump_raw``) in the
stream-0 :class:`~tools.mobile.tree_source.TreeSource` interface and registers
at priority 100, so it is tried after any faster source the tester opted in to.

The executor keeps its own single-flight guard (``dump_latency``) around the
adb dump; this class is the interface face of the same call, for a caller that
selects a source by name.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from tools.mobile import tree_source
from tools.mobile.tree_source import TreeResult


async def _guarded_dump(serial: str) -> dict:
    # Late import: executor imports most of the package. dump_raw is the
    # single-flight guard every raw dump outside the executor must take.
    from tools.mobile import executor  # noqa: PLC0415

    return await executor.dump_raw(serial)


class DumpTreeSource:
    name = tree_source.FALLBACK_SOURCE

    async def available(self, serial: str) -> bool:
        return True

    async def dump(self, serial: str, timeout_s: float) -> Optional[TreeResult]:
        started = time.monotonic()
        try:
            raw = await asyncio.wait_for(
                _guarded_dump(serial), max(0.0, float(timeout_s))
            )
        except asyncio.TimeoutError:
            return None
        content = (raw or {}).get("content")
        if (raw or {}).get("error") or not isinstance(content, str) or not content:
            return None
        elapsed = int((time.monotonic() - started) * 1000)
        return TreeResult(xml=content, elapsed_ms=elapsed, source=self.name)


def register_dump_source() -> None:
    """Register the fallback at priority 100. Idempotent (a name replaces itself)."""
    tree_source.register_tree_source(DumpTreeSource(), priority=100)
