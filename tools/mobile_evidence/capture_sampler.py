"""The socket-table sampler that gives the network capture its owner column.

Live samplers are kept by ``(run_id, tc_id)`` in :data:`_NET_SAMPLERS`, IN MEMORY
ONLY and never written: this is a socket table, not evidence, and the summary is
what is kept. A sampler polls the four ``/proc/net`` tables while one case
replays; it never raises past itself, and the number of live samplers is bounded.
"""

from __future__ import annotations

import asyncio
import logging
import time

from config.settings_guard import never_raise
from tools.mobile import adb
from tools.mobile_evidence import capture_consts as _consts
from tools.mobile_evidence import netattr

logger = logging.getLogger("tools.mobile_evidence.capture")

#: Live samplers, by ``(run_id, tc_id)``. IN MEMORY ONLY and never written:
#: this is a socket table, not evidence, and the summary is what is kept.
_NET_SAMPLERS: dict = {}

# ALL FOUR tables. The UDP pair was never read, so a DNS row could
# never be attributed to anyone -- every one of them reported "owner
# not sampled", which reads as bad luck rather than as a question
# this code had not asked.
_PROC_TABLES = (
    ("/proc/net/tcp", "tcp"),
    ("/proc/net/tcp6", "tcp"),
    ("/proc/net/udp", "udp"),
    ("/proc/net/udp6", "udp"),
)


async def _sample_once(serial: str, state: dict) -> None:
    for path, proto in _PROC_TABLES:
        result = await adb.shell(serial, ["cat", path])
        if result.get("error"):
            continue
        rows = netattr.parse_proc_net(
            str((result.get("content") or {}).get("out") or ""), proto
        )
        state["samples"] = netattr.merge_samples(state["samples"], rows)
        sample = netattr.EndpointSample(rows, state.get("uid"), time.time() * 1000)
        state["endpoints"] = netattr.merge_endpoints(state.get("endpoints"), sample)


async def _sample_loop(serial: str, state: dict) -> None:
    """Poll the socket table while the case replays. Never raises past itself."""
    # pragma: no cover - defensive
    with never_raise(
        logger,
        "mobile_evidence.capture: the socket sampler stopped early",
        level=logging.ERROR,
        exc_info=True,
    ):
        try:
            while state["taken"] < _consts.MAX_NETWORK_SAMPLES:
                await _sample_once(serial, state)
                state["taken"] += 1
                await asyncio.sleep(_consts.NETWORK_SAMPLE_INTERVAL_S)
        except asyncio.CancelledError:
            raise


def _stop_sampler(key) -> dict:
    state = _NET_SAMPLERS.pop(key, None)
    if not isinstance(state, dict):
        return {}
    task = state.get("task")
    if task is not None:
        # a finished task needs no cancel
        with never_raise():
            task.cancel()
    return state


def _start_sampler(serial: str, key: tuple, uid: object = None) -> None:
    key = (str(key[0]), str(key[1]))
    _stop_sampler(key)
    while len(_NET_SAMPLERS) >= _consts.MAX_LIVE_SAMPLERS:
        _stop_sampler(next(iter(_NET_SAMPLERS)))
    state = {"samples": {}, "endpoints": {}, "uid": uid, "taken": 0, "task": None}
    _NET_SAMPLERS[key] = state
    try:
        state["task"] = asyncio.ensure_future(_sample_loop(serial, state))
    except RuntimeError:  # pragma: no cover - there is always a loop here
        # No running loop: the samples stay empty and every row is reported
        # unsampled, which is the honest answer rather than a missing section.
        state["task"] = None
