"""Benching the fast reader instead of dropping it for the whole run.

Measured: after three stale verdicts (right after Login) the provider was cleared
and every later dump cost a ~2.2 s uiautomator read, with no way back. Now the
provider is replaced by a benched stand-in: it refuses at once (so the caller's
one uiautomator dump is the only cost), and every :data:`PROBE_EVERY` calls it
probes the real reader with one cheap fast dump. A healthy probe re-arms the
guarded provider with one strike left, so a still-stale service benches again.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from tools.mobile import a11y_strikes, adb, step_timing

logger = logging.getLogger("tools.mobile.a11y")

#: Benched calls between two health probes (a count of steps, not a timer).
PROBE_EVERY = 4
BENCHED_NOTE = "a11y_benched"
BENCHED_REFUSAL = "the a11y reader is benched; reading with uiautomator"

Probe = Callable[[str], Awaitable[dict]]
#: serial -> (real provider, probe, calls since benched).
_BENCH: dict[str, tuple] = {}


def forget(serial: str) -> None:
    _BENCH.pop(str(serial), None)


def is_benched(serial: str) -> bool:
    return str(serial) in _BENCH


def bench(serial: str, probe: Probe) -> None:
    """Replace *serial*'s provider with the benched stand-in; *probe* is one fast read."""
    key = str(serial)
    real = adb._DUMP_PROVIDERS.get(key)
    if real is None:
        adb.clear_dump_provider(key)
        return
    if key in _BENCH:
        return
    _BENCH[key] = (real, probe, 0)
    adb.clear_dump_provider(key)  # also forgets the stale event clock
    adb.set_dump_provider(key, _benched)


async def _healthy(probe: Probe, serial: str) -> bool:
    got = await probe(serial)
    return not got.get("error") and bool(got.get("content"))


async def _benched(serial: str) -> dict:
    entry = _BENCH.get(str(serial))
    if entry is None:
        return {"error": BENCHED_REFUSAL, "content": None}
    real, probe, calls = entry
    _BENCH[str(serial)] = (real, probe, calls + 1)
    if (calls + 1) % PROBE_EVERY or not await _healthy(probe, serial):
        step_timing.note(BENCHED_NOTE)
        return {"error": BENCHED_REFUSAL, "content": None}
    _BENCH.pop(str(serial), None)
    logger.warning("mobile.a11y: reader healthy on %s; re-armed", serial)
    adb.set_dump_provider(serial, real)
    a11y_strikes.reset(str(serial), left=1)
    return await real(serial)
