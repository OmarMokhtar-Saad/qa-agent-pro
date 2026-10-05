"""The core of ``qa_mobile_stop``: end ONE run, never guess which.

A target is a run id or a device serial. When it does not name exactly one
active run, the decision goes through :func:`resolve.resolve_or_ask` and an
unresolved answer stops nothing. The handler (stream W) supplies the ports.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol

from tools.mobile import lock_reaper, resolve, run_verdict

logger = logging.getLogger(__name__)

STOP_REASON = "stopped by tester"

_FREE_LOCK = "free its device lock"


@dataclass(frozen=True)
class ActiveRun:
    """One run that holds a lease right now."""

    run_id: str
    serial: str = ""
    goal: str = ""
    last_refusal: str = ""
    trace_verdict: Optional[Mapping] = None


class StopPorts(Protocol):
    """Each port may be a plain function or a coroutine function."""

    def active_runs(self, serial: Optional[str] = None) -> Any: ...

    def stop_heartbeat(self, run_id: str) -> Any: ...

    def finalize(self, run_id: str, verdict: run_verdict.Verdict) -> Any: ...

    def release(self, serial: str, run_id: str) -> Any: ...


@dataclass(frozen=True)
class StopResult:
    ok: bool
    message: str
    run_id: str = ""
    serial: str = ""
    verdict: Optional[run_verdict.Verdict] = None


def _candidates(runs: list) -> list:
    return [
        resolve.Candidate(key=r.run_id, label=r.serial, detail=str(r.goal or "")[:60])
        for r in runs
    ]


def _by_id_or_serial(text: str, cand: resolve.Candidate) -> bool:
    """Whole run id or whole serial; a prefix never selects a device."""
    wanted = " ".join(str(text or "").lower().split())
    return bool(wanted) and wanted in (cand.key.lower(), cand.label.lower())


async def _stop_one(run: ActiveRun, ports: StopPorts, reason: str) -> StopResult:
    verdict = run_verdict.synthesize(reason, run.last_refusal, run.trace_verdict)
    steps = (
        ("stop its heartbeat", ports.stop_heartbeat, (run.run_id,)),
        ("record its verdict", ports.finalize, (run.run_id, verdict)),
        (_FREE_LOCK, ports.release, (run.serial, run.run_id)),
    )
    failed = []
    for what, port, args in steps:
        try:
            await lock_reaper.call_port(port, *args)
        except lock_reaper.PORT_FAILURES:
            logger.warning(
                "stop_run: could not %s for %s", what, run.run_id, exc_info=True
            )
            failed.append(what)
    lock_reaper.forget(run.run_id)
    where = "device " + run.serial if run.serial else "the device"
    message = (
        "Stopped run "
        + run.run_id
        + " on "
        + where
        + ". Verdict "
        + verdict.line()
        + "."
    )
    if failed:
        message += " Could not " + ", ".join(failed) + "; the lock expires on its own."
    return StopResult(
        _FREE_LOCK not in failed, message, run.run_id, run.serial, verdict
    )


async def stop(
    target: Optional[str],
    ports: StopPorts,
    ask: Optional[resolve.AskCb] = None,
    reason: str = STOP_REASON,
) -> StopResult:
    """Stop the run *target* names (run id or serial); never touches another.

    An empty or ambiguous target with several active runs asks; with no way to
    ask (or no answer) it stops nothing and returns the text menu.
    """
    runs = list(await lock_reaper.call_port(ports.active_runs, None) or [])
    if not runs:
        return StopResult(False, "No active mobile run to stop.")
    found = await resolve.resolve_or_ask(
        str(target or ""),
        _candidates(runs),
        noun="run",
        ask=ask,
        match=_by_id_or_serial,
    )
    chosen = next((r for r in runs if r.run_id == found.value), None)
    if not found.resolved or chosen is None:
        return StopResult(False, found.menu())
    return await _stop_one(chosen, ports, reason)
