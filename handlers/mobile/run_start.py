"""Part of the mobile_run split (see handlers/mobile/run.py): start."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import tools.mobile.session_runs as session_runs
from config.settings_guard import never_raise
from handlers import _common
from handlers.mobile.run_cases import _CaseAsk, _mobile_collect_cases
from handlers.mobile.run_explore import _mobile_start_explore
from handlers.mobile.run_hooks import mobile_run
from handlers.mobile.run_request import _MobileStartContext
from handlers.mobile.run_suite import _mobile_start_suite, _SuiteAsk
from tools.mobile import adb as mobile_adb
from tools.mobile import emulator
from tools.untrusted import single_line as _safe

logger = logging.getLogger("tools.mcp_handlers")


async def _mobile_avd_of(serial: str) -> str:
    """The AVD name behind *serial*, or ``""`` (2026-09-03, D1 follow-up).

    ``plan_suite_run`` / ``plan_explore_run`` default a missing ``avd`` to
    ``provisioner.AVD_NAME``, and that default is what made D1 come BACK on
    resume: a run that had adopted the tester's own emulator wrote
    ``avd: "qa-agents-api35"`` into its manifest, so when that device was gone
    ``session.ensure_run_device`` re-booted the hardcoded AVD instead of the
    one the run was actually driving. Asking the device settles it once, at the
    only moment the answer is both knowable and needed. Never raises: an
    unanswerable probe returns ``""`` and the planners keep their old default.
    """

    with never_raise(logger, "mobile: could not read the AVD name for a serial"):
        named = await emulator.avd_name_of(str(serial or ""))
        return str((named or {}).get("content") or "")
    return ""


def _mobile_resume_hint() -> str:
    """The reply for the "resume" start-menu choice: a pointer, never a run."""

    return "⚠️ To resume, call `qa_mobile_test` with the `run_id`. " + _safe(
        (
            "Recent runs: "
            + ", ".join(
                "`" + str(row.get("run_id")) + "`"
                for row in ((session_runs.list_runs(5) or {}).get("content") or [])
            )
        )
        or "No runs have been recorded on this machine yet.",
        400,
    )


async def _mobile_device_probe(serial: str) -> tuple[dict, str]:
    """``(device_facts, avd)`` for *serial*, read once at run creation."""
    # ONE PRODUCER, at the one moment a run is created. Kind, model and API
    # level are read here and STORED on the manifest; every later reader --
    # status, the report, a resume in another chat -- reads that record instead
    # of asking the device again, so two readers cannot disagree and a finished
    # run still says what it ran on after the device is gone.

    # The AVD name is asked FOR THE SAME DEVICE AT THE SAME TIME (S13): both
    # are read-only probes, and asking one after the other doubled the round
    # trips before the first screen. `_mobile_avd_of` never raises, so a
    # `device_facts` failure propagates exactly as it did alone.
    facts_result, avd = await asyncio.gather(
        mobile_adb.device_facts(serial), mobile_run.mobile_avd_of(serial)
    )
    return (facts_result or {}).get("content") or {}, avd


@dataclass(frozen=True)
class _StartAsk:
    """Every argument of ``_mobile_start``, packed."""

    picked: str
    suite_id: str
    goal: str
    cases: str
    package: str
    serial: str
    pre_run_owner: str
    charter: str = ""
    progress: _common.ProgressCb = None
    new_run: bool = False
    locale: object = None
    capture: object = None
    reset_app: object = None
    permissions: object = None


# Notes for ``_mobile_start`` (moved out of the body to keep it short):
#
#     Returns ``(markdown, handed_off)``. ``handed_off`` is True once the device
#     lock has been RELABELLED from *pre_run_owner* to this run's id -- the
#     caller's signal to stop owning it, because the run does now and keeps it
#     across calls. Every other return (also a FAILED relabel) leaves it False so
#     the caller's ``finally`` gives the device straight back and no lock is
#     stranded. The relabel happens HERE because ``_mobile_next`` may release the
#     lock, and a release under a stale owner label would silently leak the hold.
#
async def _mobile_start(ask: _StartAsk) -> tuple:
    """Turn a start-menu choice into a planned run and its first packet.

    Dispatch: resume hint, explore or a case-bearing run.
    """
    if ask.picked == "resume":
        return _mobile_resume_hint(), False
    ctx = await _start_context(ask)
    if ask.picked == "explore":
        return await _mobile_start_explore(ask.charter, ask.goal, ctx)
    return await _start_with_cases(ask, ctx)


async def _start_context(ask: _StartAsk) -> _MobileStartContext:
    """Probe the device once and pack it with the run's settings."""
    device_facts, avd = await _mobile_device_probe(ask.serial)
    return _MobileStartContext(
        package=ask.package,
        serial=ask.serial,
        avd=avd,
        device_facts=device_facts,
        locale=ask.locale,
        capture=ask.capture,
        reset_app=ask.reset_app,
        permissions=ask.permissions,
        owner=ask.pre_run_owner,
        progress=ask.progress,
    )


async def _start_with_cases(ask: _StartAsk, ctx: _MobileStartContext) -> tuple:
    """Collect the cases the choice names, then plan and hand off the run."""
    reply, collected, filters = await _mobile_collect_cases(
        _CaseAsk(ask.picked, ask.suite_id, ask.cases, ask.progress)
    )
    if reply:
        return reply, False
    suite = _SuiteAsk(ask.picked, collected, filters, ask.new_run)
    return await _mobile_start_suite(suite, ctx)
