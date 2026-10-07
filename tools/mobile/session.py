"""The mobile lane's state machine, and every byte of durable state it needs.

Three properties this module exists to hold, each of which cost this programme
a defect somewhere else before it was written down:

1. **No state lives in memory.** The MCP process restarts on every ``.env`` and
   code edit, and a tester's editor restarts routinely, so a flow that remembers
   anything between two tool calls is a flow that loses a run. Every state is
   reconstructed by :func:`resolve` from ``runs/<run_id>/`` plus ``state/``, and
   this module has no mutable module-level object -- pinned by a property test,
   because "we did not happen to cache anything this time" is not the same
   claim.
   The ONE sanctioned in-process hold is :mod:`tools.mobile.held_inputs`
   (fix round 3, item 1): a tester's secret may never reach the run folder,
   so it is held in THAT module's memory -- bounded, on a sliding TTL -- and a
   restart costs the tester one more question, never a run. It lives outside
   this module so the property test keeps holding here.

2. **Nothing here waits on the device longer than a tool call may last.** A
   client kills a tool call at roughly 50 seconds, and both
   ``emulator.boot`` (240s) and ``adb.install`` (300s) can exceed that. So
   :func:`ensure_device` uses ``emulator.start`` plus a bounded
   ``emulator.wait_boot``, never ``emulator.boot``; and :func:`start_install`
   spawns adb DETACHED and decides completion by asking the DEVICE
   (``adb.installed_packages``) rather than by reaping a pid. A pid that exited
   is not evidence of an install, and an install that failed must not read as
   success.

3. **The credential is never in a structure this module stores.** A tester's
   value arrives on one submit call, lives in
   ``executor.Context.tester_inputs`` for that call, and is typed to the device
   through the IME's stdin. :func:`audit_detail` has NO parameter that could
   carry it -- it takes the field NAME and emits a marked
   ``{"secret": True, "value": "***"}`` entry. That is deliberate:
   ``run_store.redact`` masks marked objects and credential-NAMED keys, so a
   value under a tester-chosen key like ``tenantToken`` with no marker would go
   to disk in clear, and the only place that can mark it is here.

The lease identity is a token this module MINTS, because stdio MCP has no chat
identity: one process serves every chat in an editor. :func:`mint_session`
returns it, every packet echoes it back as ``session_token``, and a chat that
resumes with a ``run_id`` and no token takes the run over on purpose. The
displaced chat learns at its next call. ``run_store.acquire_lease`` is
read-decide-write with a compare-after-swap that NARROWS that race and does not
close it, which is stated here and in the tester-facing text rather than
improved upon in prose.

**This module does not read the kill-switch.** Its one reader in ``tools/mobile``
is ``downloader.download`` -- the place that spends bytes; the other is
``mobile_capture/cert.install``. The device effects this module starts
run without it; tests/mobile/test_mobile_killswitch_surface.py derives that
scope and fails on any new reader.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import secrets
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from tools.device_manager import _valid_device_id, valid_package_name
from tools.mobile import actions as actions_mod
from tools.mobile import (
    adb,
    carry_policy,
    case_runner,
    downloader,
    emulator,
    executor,
    explore_runner,
    heartbeat,
    importers,
    lock_reaper,
    locks,
    media,
    paths,
    platform_info,
    preflight,
    run_finalize,
    run_store,
    scheduler,
    sdk_locator,
    stop_run,
)

# ITS OWN LINE, after the parenthesised block: ruff's isort (I001) wants an
# ALIASED import separated, exactly as ``explore_runner`` already writes it.
from tools.mobile import charter as charter_mod
from tools.mobile.providers import composite
from tools.mobile_evidence import capture

logger = logging.getLogger(__name__)

STATE_NEEDS_FLAG = "needs_flag"
STATE_PROVISIONING = "provisioning"
#: ``ensure_run_device`` only: the run's device is gone and the AVD it came from
#: no longer exists, so there is nothing to re-boot. Never written to a manifest;
#: its one consumer, ``mcp_handlers._mobile_resume_device_stage``, answers it
#: with the no-AVD setup guide.
STATE_NO_AVD = "no_avd"
STATE_BOOTING = "booting"
STATE_NEEDS_APP = "needs_app"
STATE_PREFLIGHT = "preflight"
STATE_MENU = "menu"
STATE_RUNNING = "running"
STATE_GATE = "gate"
STATE_REPORT = "report"
STATE_TAKEN_OVER = "taken_over"
STATE_BUSY = "busy"

#: An unfinished run whose lease heartbeat stopped (2026-09-03, D7). NOT a
#: takeover: nobody else holds it, the chat that did simply went away -- so the
#: tester is told it can be picked up rather than that it is gone. Distinct
#: from STATE_TAKEN_OVER, which names a run another chat IS driving.
STATE_ABANDONED = "abandoned"

#: Every state a handler can be in, in flow order. Also the set the tests assert
#: against, so a state that silently stops being reachable fails the suite.
STATES: tuple[str, ...] = (
    STATE_NEEDS_FLAG,
    STATE_PROVISIONING,
    STATE_BOOTING,
    STATE_NEEDS_APP,
    STATE_PREFLIGHT,
    STATE_MENU,
    STATE_RUNNING,
    STATE_GATE,
    STATE_ABANDONED,
    STATE_REPORT,
    STATE_TAKEN_OVER,
    STATE_BUSY,
)

#: The longest ONE tool call may run before it hands back a pointer instead of
#: an answer. Clients kill a tool call at around 50 seconds and a killed call is
#: indistinguishable from a broken server, so this bound is STRUCTURAL: it is
#: passed in, checked at the two places that start slow device work, and never
#: asserted against wall-clock in a test -- a test that measured elapsed time
#: here would be asserting below its own noise floor.
CALL_BUDGET_S = 45

#: The most tester-supplied fields one submit may carry via ``tester_inputs``
#: (see :func:`_merge_tester_inputs`). A real login/registration turn asks for
#: a handful of fields -- a password, a one-time code -- never dozens; the cap
#: exists so an oversized multi-field payload cannot grow the audit's
#: ``tester_fields`` list, or the executor's per-field lookups, without bound.
MOBILE_MAX_TESTER_INPUTS = 8

#: The longest this module will ever wait on the device inside one tool call.
#: Well under a client's ~50s tool timeout, and passed EXPLICITLY to
#: ``wait_boot`` -- whose own default is 240s, so an omitted keyword is the
#: defect this constant exists to prevent.
DEVICE_WAIT_BUDGET_S = 20

#: The tail of :data:`CALL_BUDGET_S` reserved, once, for one best-effort
#: screen capture when a busy reply is about to go out with nothing in it.
#: ``next_packet`` spends it on :func:`_final_packet` instead of starting the
#: slow relaunch/foreground-poll path this reserve exists to avoid overrunning.
PACKET_RESERVE_S = 8

INSTALL_FILE = "install.json"

LANE_SUITE = "suite"
LANE_EXPLORE = "explore"

_RUN_PREFIX = "mrun-"


@dataclass(frozen=True)
class Budget:
    """A monotonic deadline for ONE tool call.

    A value object, so a test constructs an ALREADY-EXPIRED budget instead of
    waiting for one -- which is what keeps the early-return tests structural.
    """

    deadline: float

    def remaining(self) -> float:
        return max(0.0, float(self.deadline) - time.monotonic())

    def expired(self) -> bool:
        return time.monotonic() >= float(self.deadline)


def new_budget(seconds: float = CALL_BUDGET_S) -> Budget:
    """A budget starting now, never longer than :data:`CALL_BUDGET_S`."""
    span = min(float(seconds or CALL_BUDGET_S), float(CALL_BUDGET_S))
    return Budget(deadline=time.monotonic() + max(1.0, span))


def _busy(resolved: object, tc_id: str, *, packet: object = None) -> dict:
    """The bounded-call reply: usually a pointer, and never half-done.

    *packet* is the reserved-tail capture from :func:`_final_packet` -- a
    best-effort screen, or ``None`` when the device did not answer inside
    :data:`PACKET_RESERVE_S`. Passing it through here, rather than building it
    at the call site, keeps every caller's shape the same.
    """
    return {
        "error": None,
        "content": {
            "state": STATE_BUSY,
            "packet": packet,
            "tc_id": str(tc_id or ""),
            "resolved": resolved if isinstance(resolved, dict) else {},
        },
    }


def _in_reserve(budget: object) -> bool:
    """Expired, or inside the reserved tail. ``expired()`` is asked first so a
    budget without ``remaining()`` (a duck-typed test budget) still works."""
    if budget.expired():
        return True
    remaining = getattr(budget, "remaining", None)
    return callable(remaining) and remaining() <= PACKET_RESERVE_S


async def _final_packet(ctx: object, budget: object) -> dict | None:
    """One best-effort screen capture spent from the reserved tail of a busy
    reply. Never raises -- a device that will not answer in time degrades the
    busy reply to plain text, exactly as before this change.

    Reuses the SAME primitive ``case_runner.start_case`` uses to prune a fresh
    dump into a screen (``adb.uiautomator_dump``/``display_size``/
    ``display_density`` feeding :func:`composite.observe`), with no relaunch,
    no foreground poll and no activity resolve -- those are the slow parts
    this fix exists to avoid starting this close to the client's own timeout.

    The whole capture runs under ``asyncio.wait_for`` with what is left of
    *budget* as its timeout, so a device that stalls on any of the three adb
    calls is cut off at the budget deadline and the call never overruns
    :data:`CALL_BUDGET_S`.
    """
    serial = str(getattr(ctx, "serial", "") or "")
    remaining = getattr(budget, "remaining", None)
    if not serial or not callable(remaining):
        return None

    async def capture() -> dict | None:
        dumped = await executor.dump_raw(serial)
        if dumped.get("error"):
            return None
        sized = await adb.display_size(serial)
        dpi = await adb.display_density(serial)
        pruned = composite.observe(
            dumped.get("content"),
            "",
            display=sized.get("content"),
            density=dpi.get("content"),
        )
        if pruned.get("error"):
            return None
        return pruned.get("content") or None

    try:
        return await asyncio.wait_for(capture(), timeout=max(0.0, remaining()))
    except asyncio.TimeoutError:
        logger.info("mobile.session: _final_packet capture hit the call budget")
        return None
    except Exception:  # pragma: no cover - defensive, see docstring
        logger.warning("mobile.session: _final_packet capture failed", exc_info=True)
        return None


def mint_session() -> str:
    """A lease identity for one chat. Matches ``run_store``'s session pattern."""
    return "s-" + secrets.token_hex(8)


def mint_run_id() -> str:
    """A run id a tester can read back to us. Matches ``run_store.valid_run_id``."""
    return _RUN_PREFIX + time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)


# ---------------------------------------------------------------------------
# Lease
# ---------------------------------------------------------------------------


def claim(run_id: str, session_token: str = "", *, force: bool = False) -> dict:
    """Take or refresh this chat's hold on a run.

    ``{"error", "content": {"session_token", "state", "holder",
    "taken_over_from"}}`` where ``state`` is ``run_store.HELD`` or
    :data:`STATE_TAKEN_OVER`.

    A caller with NO token is a fresh chat, and a fresh chat naming a run id is
    an explicit resume, so it takes over (``force=True``). A caller with a token
    that is no longer the holder is told so and gets no packet -- that decision
    is here rather than in the handler because both handlers need it and a
    duplicated "am I still the holder?" test is how the two drift.
    """
    try:
        refusal = _claim_refusal(run_id)
        if refusal:
            return refusal
        token = str(session_token or "").strip()
        fresh = not token
        if fresh:
            token = mint_session()
        result = run_store.acquire_lease(run_id, token, force=bool(force or fresh))
        if result.get("error"):
            return result
        body = result.get("content") or {}
        if not body.get("acquired"):
            # The displaced holder's exit. Returning HELD here would let this
            # chat keep producing packets for an emulator another chat is
            # driving, which is the whole failure the lease exists to stop.
            return _claim_reply(token, STATE_TAKEN_OVER, str(body.get("holder") or ""))
        status = run_store.touch_lease(run_id, token)
        state = str((status.get("content") or {}).get("state") or run_store.HELD)
        if state == run_store.TAKEN_OVER:
            holder = str((status.get("content") or {}).get("holder") or "")
            return _claim_reply(token, STATE_TAKEN_OVER, holder)
        return _claim_reply(
            token,
            run_store.HELD,
            token,
            str(body.get("taken_over_from") or ""),
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.claim failed")
        return {"error": str(exc), "content": None}


def _claim_reply(
    token: str, state: str, holder: str, taken_over_from: str = ""
) -> dict:
    """The ``claim`` success envelope."""
    return {
        "error": None,
        "content": {
            "session_token": token,
            "state": state,
            "holder": holder,
            "taken_over_from": taken_over_from,
        },
    }


def _claim_refusal(run_id: str) -> dict | None:
    """The error reply when ``run_id`` cannot be claimed (absent or ended), else None."""
    # EXISTENCE FIRST. acquire_lease mkdir -p's the run directory, so
    # claiming before checking let any well-formed id mint a run in the
    # shared state root -- and the reply then said the run did not exist
    # while its lease sat on disk. The check lives in claim rather than in the
    # handlers: two copies of one test is how the two handlers drift.
    on_disk = run_store.read_manifest(run_id).get("content") or {}
    if not on_disk:
        return {
            "error": (
                "No run `" + str(run_id)[:64] + "` on this machine. "
                "Nothing was created. `qa_mobile_status` lists the runs "
                "this install knows about."
            ),
            "content": None,
        }
    stopped = run_finalize.stop_text(on_disk)
    if stopped:
        # A run ended by qa_mobile_stop or the idle release takes no more
        # steps: its verdict is recorded, and a later step would restart
        # the heartbeat and retake the device under a run that says it ended.
        return {
            "error": (
                "Run `" + str(run_id)[:64] + "` has ended (" + stopped[:40] + ") "
                "and takes no more steps. `qa_mobile_status` shows its result; "
                "start a new run to test again."
            ),
            "content": None,
        }
    return None


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------


def _no_avd_refusal() -> dict:
    """The refusal ``ensure_device`` hands back when no AVD is named."""
    return {
        "error": (
            "No emulator (AVD) is named. Call `qa_mobile_test` again with "
            "`avd` set to one of the AVDs in Android Studio's Device "
            "Manager. `emulator=list` shows them and `emulator=create` "
            "makes one from an installed system image."
        ),
        "content": None,
    }


async def ensure_device(
    *,
    avd: str = "",
    serial: str = "",
    budget_s: int = DEVICE_WAIT_BUDGET_S,
    locale: str = "",
) -> dict:
    """Attach to a booted emulator, or start one and hand back a pointer.

    ``{"error", "content": {"state", "serial", "detail"}}`` with ``state`` one of
    ``ready`` / :data:`STATE_BOOTING`.

    ``emulator.boot`` is deliberately NOT called: it polls to
    ``QA_MOBILE_BOOT_TIMEOUT_S`` (240s by default), which is four times a
    client's tool timeout, so a tester would see a dead editor rather than a
    message. The bounded pair is ``emulator.start`` (spawn, detached, returns at
    once) plus ``emulator.wait_boot`` with an EXPLICIT budget.

    That budget is :data:`DEVICE_WAIT_BUDGET_S`, and the setting does NOT move
    it, so both waits below pass ``tunable=False``: the timeout message they
    hand to the tester must not offer a knob that cannot change this wait.
    """
    try:
        name = str(avd or "").strip()
        budget = max(1, int(budget_s or DEVICE_WAIT_BUDGET_S))
        if serial:
            # A tester-chosen (or already-adopted) device: never probe for
            # the hardcoded AVD and never spawn a second emulator under it.
            return await _wait_device_ready(serial, budget)
        if not name:
            # No house AVD to fall back on: auto-provisioning is retired
            # (docs/RETIRED_CAPABILITIES.md -> 6) and the handler resolves the
            # tester's own AVD before it gets here. Refuse rather than spawn
            # `emulator -avd ""`.
            return _no_avd_refusal()
        running = await emulator.find_running(name)
        if running.get("error"):
            return running
        serial = str((running.get("content") or {}).get("serial") or "")
        if serial:
            return await _wait_device_ready(serial, budget)
        return await _spawn_device(name, locale)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.ensure_device failed")
        return {"error": str(exc), "content": None}


async def _wait_device_ready(serial: str, budget: int) -> dict:
    """Wait for ``serial`` to boot: ``ready``, or a ``booting`` pointer on timeout."""
    waited = await emulator.wait_boot(serial, timeout=budget, tunable=False)
    if waited.get("error"):
        return {
            "error": None,
            "content": {
                "state": STATE_BOOTING,
                "serial": serial,
                # 1200, not 400: wait_boot's message may carry the
                # virtualization note, which is 295 chars on top of a
                # ~173-char timeout line. At 400 every delivery of that
                # note was cut mid-parenthesis and lost the half that
                # says WHERE to enable it -- a producer composing a
                # message no consumer delivers whole.
                "detail": str(waited["error"])[:1200],
            },
        }
    return {
        "error": None,
        "content": {"state": "ready", "serial": serial, "detail": ""},
    }


async def _spawn_device(name: str, locale: str) -> dict:
    """Start the AVD ``name`` detached and hand back a ``booting`` pointer."""
    # Only this path spawns, so only this path can set the locale from
    # the first frame. A device we ADOPTED is handled by `apply_locale`,
    # which reads back rather than assuming.
    # B5: if the emulator this server spawned last time already DIED, say so in
    # its own words instead of spawning over it and reporting "booting" again.
    gone = emulator.exit_report(name)
    if gone:
        return {"error": gone["error"], "content": None}
    started = await emulator.start(name, locale=str(locale or ""))
    if started.get("error"):
        return started
    return {
        "error": None,
        "content": {
            "state": STATE_BOOTING,
            "serial": "",
            "detail": (
                name
                + " was started (pid "
                + str((started.get("content") or {}).get("pid") or 0)
                + "). A cold emulator takes a minute or two."
            ),
        },
    }


async def device_alive(serial: str) -> dict:
    """``{alive, state}`` for one serial, answered by ``adb devices``.

    Keyed on the SERIAL rather than on ``emulator.find_running``'s AVD lookup
    deliberately: a resumed run knows the serial it was driving, and the AVD
    lookup needs a second round trip to a device that may already be gone.
    """
    try:
        wanted = str(serial or "").strip()
        if not wanted:
            return {"error": None, "content": {"alive": False, "state": "unknown"}}
        listed = await adb.devices()
        if listed.get("error"):
            return {"error": None, "content": {"alive": False, "state": "unknown"}}
        for found in listed.get("content") or []:
            if str(found) == wanted:
                return {
                    "error": None,
                    "content": {"alive": True, "state": "device"},
                }
        return {"error": None, "content": {"alive": False, "state": "absent"}}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.device_alive failed")
        return {"error": str(exc), "content": None}


async def ensure_run_device(run_id: str, *, budget: Budget | None = None) -> dict:
    """Make sure the emulator THIS RUN was driving is up, re-booting if it died.

    ``{"error", "content": {"state", "serial", "detail", "rebooted"}}`` with
    ``state`` ``ready``, :data:`STATE_BOOTING`, or :data:`STATE_NO_AVD` (plus
    ``avd``) when the AVD this run came from no longer exists.

    A resume can arrive days later, in a new chat, on a machine that has been
    rebooted since -- at which point the manifest names a serial that no longer
    exists and every adb call fails with a device error a tester cannot act on.
    So the serial is CHECKED. A re-booted emulator can also come back on a
    DIFFERENT serial, which is why the fresh one is written back to the manifest
    and why ``rebooted`` is reported: the caller must re-run the preflight
    before producing a packet against a device it has not checked.
    """
    try:
        manifest = (run_store.read_manifest(run_id) or {}).get("content")
        if not isinstance(manifest, dict) or not manifest:
            return {
                "error": (
                    "No run `"
                    + str(run_id)
                    + "` on this machine. `qa_mobile_status` with no run id "
                    "lists the runs that do exist."
                ),
                "content": None,
            }
        serial = str(manifest.get("serial") or "")
        alive = (await device_alive(serial)).get("content") or {}
        if alive.get("alive"):
            return _run_device_reply("ready", serial)
        return await _reboot_run_device(run_id, manifest, budget)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.ensure_run_device failed")
        return {"error": str(exc), "content": None}


def _run_device_reply(
    state: str, serial: str, detail: str = "", *, rebooted: bool = False
) -> dict:
    """The reply shape ``ensure_run_device`` returns."""
    return {
        "error": None,
        "content": {
            "state": state,
            "serial": serial,
            "detail": detail,
            "rebooted": rebooted,
        },
    }


async def _reboot_run_device(
    run_id: str, manifest: dict, budget: Budget | None
) -> dict:
    """Boot the run's AVD again (the serial is gone) and write back a new serial."""
    serial = str(manifest.get("serial") or "")
    avd = str(manifest.get("avd") or "")
    # The device is gone and must be booted again -- but only from an AVD
    # that still exists. Spawning a missing one "succeeds" (the launcher is
    # detached) and then times out as a boot, which tells the tester to wait
    # for something that will never come up. A failed listing (no SDK)
    # falls through to the spawn, whose own error says what is missing.
    listed = await emulator.list_avds()
    if not listed.get("error") and avd not in (listed.get("content") or []):
        reply = _run_device_reply(STATE_NO_AVD, "")
        reply["content"]["avd"] = avd
        return reply
    span = int(budget.remaining()) if budget is not None else DEVICE_WAIT_BUDGET_S
    ready = await ensure_device(
        avd=avd, budget_s=max(1, min(DEVICE_WAIT_BUDGET_S, span))
    )
    if ready.get("error"):
        return ready
    state = ready.get("content") or {}
    if str(state.get("state") or "") != "ready":
        return _run_device_reply(
            STATE_BOOTING,
            str(state.get("serial") or ""),
            str(state.get("detail") or ""),
        )
    fresh = str(state.get("serial") or "")
    if fresh and fresh != serial:
        manifest["serial"] = fresh
        run_store.write_manifest(run_id, manifest)
    return _run_device_reply("ready", fresh or serial, rebooted=True)


def _install_state_path() -> Path:
    return paths.state_file(INSTALL_FILE)


#: SECONDS an install record may be old and still support the claim "the
#: install is still running". The record lives at ONE machine-wide path with no
#: run identity, exactly like the provisioning record ``render`` ages, so past
#: this age it describes an install the reader never started. Sized off the
#: thing it bounds: an `adb install -r -g` of a large APK over a slow emulator
#: bridge is minutes, not hours, so an hour is generous for the real case and
#: still far short of the days that produced the defect. Bounded from above in
#: ``tests/mobile/test_mobile_bounds_upper.py``.
INSTALL_PENDING_MAX_AGE_S = 3600


def _started_seconds(record: dict) -> float:
    """The record's start time as a float, or ``0.0`` for anything unusable.

    This was ``float(record.get("started") or 0)`` inline. ``float()`` raises
    OverflowError on a sufficiently large int and ValueError on a non-numeric
    string, and the exception unwound into ``install_state``'s outer
    ``except Exception`` -- so one corrupt field in a machine-wide file turned
    a routine "is this app installed" answer into a FAILED CALL, which the
    caller renders as a broken device rather than as a healthy no.

    Found by strengthening the oversized-stamp test to assert the ENVELOPE
    rather than only the verdict: the aging guard beside it was catching its
    own OverflowError correctly, and this second coercion was not.
    """
    try:
        started = float(record.get("started") or 0)
    except (OverflowError, ValueError, TypeError):
        return 0.0
    # A NON-FINITE VALUE DOES NOT RAISE, so the except above never sees it.
    # `json.loads` accepts bare NaN, Infinity and -Infinity, and this record is
    # a machine-wide file this process did not write. Every comparison against
    # NaN is False, so a NaN returned here walks through the freshness checks
    # downstream instead of tripping them -- the same shape that let a NaN
    # timestamp make the run collector delete a live run. `or 0` is a default
    # for a FALSY value and NaN is truthy, so it is not a guard either.
    if not math.isfinite(started):
        return 0.0
    return started


def _install_is_recent(record: dict, now: float) -> bool:
    """Did the install this record describes start recently enough to still run?

    Same rule, same reason, as ``render._record_is_current``: a machine-wide
    record with no identity is only as good as its age, so a MISSING or
    non-numeric ``started`` is not recent -- an undated record cannot support a
    claim about now -- and neither is a stamp in the future.
    """
    stamp = record.get("started")
    if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
        return False
    try:
        age = float(now) - float(stamp)
    except (OverflowError, ValueError):
        return False
    return 0 <= age <= INSTALL_PENDING_MAX_AGE_S


def _read_install_record() -> dict:
    """The last started install, or ``{}``.

    Unreadable is deliberately not an error: the DEVICE is the authority on
    whether the package is present, and this record only says what was started.
    """
    try:
        target = _install_state_path()
        if not target.is_file():
            return {}
        body = json.loads(target.read_text(encoding="utf-8"))
        return body if isinstance(body, dict) else {}
    except Exception:
        logger.info("mobile.session: unreadable install state")
        return {}


def start_install(serial: str, apk_path: str, package: str) -> dict:
    """Spawn ``adb install`` DETACHED and record what was started.

    ``adb.install`` is bounded at 300s, which no tool call may hold, so the
    install outlives this call by design. Completion is NOT inferred from the
    process: :func:`install_state` asks the device whether the package is
    present, because a pid that exited proves nothing and an install that failed
    must never read as success.

    Reads NO switch: the mobile lane ships on with no flag. The handler that
    normally calls this checks the lane predicate and ``apply``, and those are
    guards on that CALLER only, while this function spawns a detached install
    on the tester's device -- an effect that outlives the call. A new caller
    has to bring its own consent; nothing here asks for it.
    """
    try:
        path = Path(str(apk_path or "")).expanduser()
        refusal = _install_refusal(serial, package, path)
        if refusal:
            return refusal
        paths.ensure_tree()
        proc = _spawn_install(serial, path)
        payload = {
            "package": str(package),
            "serial": str(serial),
            "apk": str(path),
            "pid": int(getattr(proc, "pid", 0) or 0),
            "started": time.time(),
        }
        downloader.write_progress(_install_state_path(), payload)
        return {"error": None, "content": payload}
    except Exception as exc:
        logger.exception("mobile.session.start_install failed")
        return {"error": str(exc), "content": None}


def _install_refusal(serial: str, package: str, path: Path) -> dict | None:
    """The error reply for an install that must not start, else None."""
    if not valid_package_name(package):
        return {
            "error": "Refusing " + repr(str(package)[:60]) + " as a package name.",
            "content": None,
        }
    if not _valid_device_id(serial):
        return {
            "error": "Refusing " + repr(str(serial)[:40]) + " as a device id.",
            "content": None,
        }
    if path.suffix.lower() != ".apk" or not path.is_file():
        return {
            "error": (
                "No .apk file at "
                + str(path)[:200]
                + ". Give the full path to the file, and nothing is installed."
            ),
            "content": None,
        }
    problem = adb.apk_problem(path)
    if problem:
        return {
            "error": (
                "Not installing "
                + str(path)[:200]
                + ": "
                + problem
                + ". Nothing was installed."
            ),
            "content": None,
        }
    return None


def _spawn_install(serial: str, path: Path) -> object:
    """Start ``adb install -r -g`` detached and return the process."""
    command = [adb.resolve_adb(), "-s", str(serial), "install", "-r", "-g", str(path)]
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    kwargs.update(platform_info.detach_kwargs())
    return subprocess.Popen(command, **kwargs)  # noqa: S603 - argv, no shell


async def install_state(serial: str, package: str) -> dict:
    """``{probed, installed, pending, apk, started}`` -- answered by the DEVICE.

    ``probed`` IS THE POINT. Whether the device answered is a fact only this
    function can know, so it SAYS so rather than leaving callers to infer it
    from the envelope. They cannot: this function's own ``error`` is set only
    in the defensive branch below, so on the failure it exists to catch it is
    ``None`` and ``probed = not state.get("error")`` reads True. A caller then

    ``pending`` ASSERTS EXACTLY WHAT THE RECORD ESTABLISHED, no wider. The
    record is one machine-wide file naming a package, a serial and a start
    time, so "the install is still running" needs all three to line up: the
    same package, the SAME DEVICE, and a start recent enough that the install
    could still be in flight. It used to need only the package name, so a
    five-day-old record from a different emulator still told the tester an
    install was in progress -- the same stale-machine-wide-record defect this
    lane fixed for the provisioning record, in the commit that fixed it.
    A record from another device is wrong at any age, which is why the serial
    is matched as well as the age.
    printed "`pkg` is not installed on the emulator" off a probe that never
    answered (2026-09-08, round 2).

    ONE PIECE OF EVIDENCE, and it is exact: ``adb.installed_packages`` reports
    an ``error`` when adb could not be run at all AND when the command ran and
    failed (a non-zero exit -- surfaced there, in the one producer of this
    list, rather than guessed at here from an empty result). So the whole rule
    is the envelope of that call, and there is no second, weaker derivation of
    it in this module.

    ``installed`` and ``pending`` are POSITIVE claims, made only from an
    answered probe: an unprobed call returns both False, so no caller can turn
    "we do not know" into "the install is still running".
    """
    try:
        record = _read_install_record()
        listed = await adb.installed_packages(serial)
        probed = not listed.get("error")
        names = list(listed.get("content") or [])
        content = _install_state_content(record, probed, names, serial, package)
        return {"error": None, "content": content}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.install_state failed")
        return {"error": str(exc), "content": None}


def _install_state_content(
    record: dict, probed: bool, names: list, serial: str, package: str
) -> dict:
    """The ``install_state`` answer, derived from one probe and the record."""
    installed = bool(probed and str(package) in names)
    pending = bool(
        probed
        and not installed
        and record.get("package") == str(package)
        and str(record.get("serial") or "") == str(serial)
        and _install_is_recent(record, time.time())
    )
    # FUZZY MATCH, FROM THE SAME PROBE. `installed_packages` already listed
    # every package id on the device to answer `installed` above -- this
    # reuses that ONE round trip rather than spending a second adb call to
    # answer "did the caller guess the wrong id". A tester (or the model
    # on their behalf) names an app, not a bundle id, and a wrong guess
    # used to dead-end in "not installed" with no way forward short of a
    # manual `adb shell pm list packages`. The cap of 5 is a LOCAL bound
    # (not a module constant): a hint in an error message, not a listing,
    # so it does not need the CEILINGS governance a shared cap would.
    suggestions: list[str] = []
    if probed and not installed and names:
        import difflib

        suggestions = difflib.get_close_matches(str(package), names, n=5, cutoff=0.4)
    return {
        "probed": bool(probed),
        "installed": installed,
        "pending": pending,
        "apk": str(record.get("apk") or ""),
        "started": _started_seconds(record),
        "suggestions": suggestions,
    }


# ---------------------------------------------------------------------------
# Planning a run
# ---------------------------------------------------------------------------


def _case_dumps(cases: object) -> list[dict]:
    """Case bodies for the manifest, so a fresh chat can rehydrate them.

    ``scheduler.plan_run`` writes only the ORDER, and nothing in Phases 1-2
    writes the cases themselves -- so a resumed chat would hold a tc_id it could
    not run. Three of the six start-menu sources never produce a stored suite
    (pasted markdown, csv, xlsx) and an exploratory run has none at all, so
    re-loading from ``suite_store`` was not an option.
    """
    out: list[dict] = []
    for case in list(cases or []):
        try:
            out.append(case.model_dump(mode="json"))
        except Exception:
            logger.warning("mobile.session: uncheckpointable case skipped")
    return out


def case_signature(package: object, cases: object) -> str:
    """An identity for "this app, these cases", stable across chats.

    Derived from the package and the SORTED (tc_id, title) pairs, so the order
    the cases were pasted in does not make a second run look like a new one,
    and a genuinely different set never collides.

    It exists because on 2026-09-04 a session started EIGHT runs of one case in
    seventeen minutes: nothing could tell the model that the run it had just
    abandoned was the one to go back to, so it planned another.
    """
    import hashlib

    pairs = []
    for case in list(cases or []):
        try:
            body = case.model_dump(mode="json") if hasattr(case, "model_dump") else case
        except Exception:  # pragma: no cover - defensive
            continue
        if not isinstance(body, dict):
            continue
        # THE STEPS TOO, not only the id and the title. Hashing the name alone
        # meant a tester who EDITED TC-001's steps and re-submitted was offered
        # the previous run -- which then executed the old steps and reported
        # them under the new case's name. A silently wrong result is worse than
        # a duplicate run, which is all this signature exists to avoid.
        steps = []
        for step in body.get("steps") or []:
            if not isinstance(step, dict):
                continue
            steps.append(
                str(step.get("action") or "")
                + "\u0000"
                + str(step.get("test_data") or "")
                + "\u0000"
                + str(step.get("expected_result") or "")
            )
        pairs.append(
            str(body.get("tc_id") or "")
            + "\u0000"
            + str(body.get("title") or "")
            + "\u0000"
            + "\u0003".join(steps)
        )
    seed = str(package or "") + "\u0001" + "\u0002".join(sorted(pairs))
    return hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[:16]


def run_signature(run_id: str) -> str:
    """The signature recorded on a run's manifest, or ``""``."""
    manifest = (run_store.read_manifest(run_id) or {}).get("content") or {}
    return str(manifest.get("case_signature") or "")


def find_resumable_run(signature: str, limit: int = 60) -> dict | None:
    """The newest UNFINISHED run of the same app and the same cases, or None.

    Deliberately keyed on package plus case set and NOT on the serial. A run is
    a durable thing on disk; a serial is which emulator happened to be attached
    when it started. A tester whose emulator was restarted, or who is resuming
    from another chat, still wants the run they were in the middle of --
    ``_mobile_resume_device_stage`` re-checks the device on every resume, and
    the lane's device lock already serialises concurrent starts, so ignoring
    the serial here adds no hazard.

    The *limit* is a real bound, stated rather than hidden: the newest 60 runs
    are considered, and a resumable run older than that is not offered. It is
    an offer, not a guarantee, and `qa_mobile_status` still lists everything.

    Never raises: this decides whether to OFFER something, and a lookup that
    failed must fall through to planning a run rather than refusing one.
    """
    try:
        wanted = str(signature or "")
        if not wanted:
            return None
        for row in (list_runs(limit) or {}).get("content") or []:
            offer = _resumable_offer(row, wanted)
            if offer:
                return offer
        return None
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile.session.find_resumable_run failed")
        return None


def _resumable_offer(row: dict, wanted: str) -> dict | None:
    """The offer for one listed run when it matches *wanted* and is unfinished."""
    manifest = row.get("manifest") or {}
    # THE KEPT SIGNATURE, and only that one.
    #
    # There was a second comparison here against `source_signature` --
    # the cases the tester supplied before the filters -- so that a
    # re-run of failures could recognise its own earlier run. Round 3
    # showed it had to be conditioned on "that run was started with no
    # filter", because otherwise a tester who ran five cases filtered
    # to two and then started the whole suite was told the earlier run
    # was "running the same cases": it was running two of five, and
    # accepting the offer finishes with three never executed and a
    # report that looks complete.
    #
    # And with that condition the clause is DEAD, which mutation then
    # proved: a run started with no filter has the two signatures EQUAL
    # by construction, so the kept comparison has already matched
    # whenever the source one could. An unkillable guard is not a
    # guard, so it is gone and `source_signature` stays on the manifest
    # as the record of what was asked for.
    if wanted != str(manifest.get("case_signature") or ""):
        return None
    # NO package argument at all. There was a package comparison here
    # and mutation proved it unreachable -- `case_signature` is seeded
    # WITH the package, so a signature match already implies it -- and
    # round 2 then pointed out that keeping the PARAMETER after
    # deleting its use is a trap for a caller who passes a package that
    # did not build the signature. The binding is in the signature, and
    # `test_a_different_package_gives_a_different_signature` pins it.
    run_id = str(row.get("run_id") or "")
    resolved = (resolve(run_id) or {}).get("content") or {}
    if not resolved or resolved.get("finished"):
        return None
    return {
        "run_id": run_id,
        "state": str(resolved.get("state") or ""),
        "done": int(resolved.get("done") or 0),
        "total": int(resolved.get("total") or 0),
    }


def _no_case_survived(ordered: dict) -> dict:
    """The refusal for a filter set that dropped every case."""
    applied = ", ".join((ordered.get("content") or {}).get("applied") or []) or "none"
    return {
        "error": (
            f"No case survived the filters, so no run was created (applied: {applied})."
        ),
        "content": None,
    }


def _run_records(
    device: object, locale: object, capture: object, reset_app: object
) -> dict:
    """The four environment records both lanes put on a new run's manifest."""
    return {
        # WHAT LANGUAGE THIS RAN IN, on the manifest for the same reason
        # `device` is: a finished run must still say so after the device
        # is gone, and two readers asking the device again could get two
        # answers.
        "locale": locale_record(locale),
        # WHAT HARDWARE THIS RAN ON, recorded once at planning time.
        # On the manifest rather than re-read per status call because a
        # finished run must still say what it ran on after the device is
        # unplugged, and because two readers asking the device again
        # could get two answers.
        "device": device_record(device),
        # WHAT TIER THIS RUN'S API CAPTURE REACHED, recorded once at
        # planning time for the same reason `locale`/`device` are: a
        # finished run must still say so after the proxy is torn down.
        "capture": capture_record(capture),
        "reset_app": reset_app_record(reset_app),
    }


def _suite_manifest(
    cases: object, kept: list, package: str, serial: str, source: str
) -> dict:
    """The suite lane's own manifest keys (identity, signatures, kept cases)."""
    return {
        "lane": LANE_SUITE,
        "package": str(package or ""),
        "serial": str(serial or ""),
        "source": str(source or ""),
        "case_signature": case_signature(package, kept),
        # The cases the TESTER supplied, before the filters -- the
        # RECORD of what was asked for, and nothing more.
        #
        # It was added so a re-run of failures could recognise its own
        # earlier run, and `find_resumable_run` no longer reads it: that
        # match had to be conditioned on "started with no filter", and
        # with that condition it was dead. The flow survives anyway,
        # because a run's manifest stores the KEPT cases, so a second
        # re-run hashes those and matches `case_signature`.
        "source_signature": case_signature(package, cases),
        "cases": _case_dumps(kept),
    }


def _explore_manifest(state: dict, package: str, serial: str, avd: str) -> dict:
    """The exploratory lane's own manifest keys."""
    return {
        "lane": LANE_EXPLORE,
        "package": str(package or ""),
        "serial": str(serial or ""),
        "avd": str(avd or ""),
        # A SNAPSHOT of the PRODUCER's answer, not of a setting.
        # sdk_locator.avd_system_image() is the one function that says which
        # image this lane runs: it reads image.sysdir.1 out of the AVD's own
        # config.ini, so it reports what the device on disk actually boots.
        # Reading qa_mobile_system_image_tag here would be a SECOND derivation
        # -- and since the tester brings their own AVD, no setting decides it.
        # "" when the AVD cannot be resolved, which the report renders as "not
        # recorded" rather than inventing today's answer for a finished run.
        "system_image": str(
            (sdk_locator.avd_system_image(avd) or {}).get("content") or ""
        ),
        "order": [],
        "total": 0,
        "cases": [],
        "explore": state,
    }


def plan_suite_run(
    cases: object,
    *,
    package: str,
    serial: str,
    source: str = "",
    filters: object = None,
    avd: str = "",
    device: object = None,
    locale: object = None,
    capture: object = None,
    reset_app: object = None,
) -> dict:
    """Order, persist and create a suite run. ``{"error", "content": {...}}``."""
    try:
        ordered = scheduler.order_cases(cases, filters)
        if ordered.get("error"):
            return ordered
        kept = list((ordered.get("content") or {}).get("cases") or [])
        if not kept:
            return _no_case_survived(ordered)
        run_id = mint_run_id()
        planned = scheduler.plan_run(
            run_id,
            kept,
            None,
            manifest_extra={
                **_suite_manifest(cases, kept, package, serial, source),
                **_run_records(device, locale, capture, reset_app),
                "avd": str(avd or ""),
            },
        )
        if planned.get("error"):
            return planned
        body = dict(planned.get("content") or {})
        body["run_id"] = run_id
        body["lane"] = LANE_SUITE
        body["dropped"] = (ordered.get("content") or {}).get("dropped") or []
        body.pop("cases", None)
        return {"error": None, "content": body}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.plan_suite_run failed")
        return {"error": str(exc), "content": None}


def plan_explore_run(
    goal: str,
    *,
    package: str,
    serial: str,
    charter: object = None,
    watch_for: object = (),
    avd: str = "",
    device: object = None,
    locale: object = None,
    capture: object = None,
    reset_app: object = None,
) -> dict:
    """Create an exploratory run whose whole state is one manifest dict."""
    try:
        text = " ".join(str(goal or "").split())
        if len(text) < 5:
            return {
                "error": (
                    "Give the exploratory goal in a sentence -- what should the "
                    "app be made to do? Nothing was started."
                ),
                "content": None,
            }
        run_id = mint_run_id()
        # The run's TERMS go into the state, which IS manifest["explore"], so a
        # resume from another chat explores under the same charter.
        # charter=None reproduces this call's previous behaviour exactly.
        state = explore_runner.new_state(text, watch_for, charter=charter)
        created = run_store.create_run(
            run_id,
            {
                **_explore_manifest(state, package, serial, avd),
                **_run_records(device, locale, capture, reset_app),
            },
        )
        if created.get("error"):
            return created
        return {
            "error": None,
            "content": {"run_id": run_id, "lane": LANE_EXPLORE, "explore": state},
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.plan_explore_run failed")
        return {"error": str(exc), "content": None}


def import_cases(source: object) -> dict:
    """A tester's own cases -> ``{cases, rejected, truncated}``. Never raises."""
    return importers.load(source)


# ---------------------------------------------------------------------------
# Reading a run back
# ---------------------------------------------------------------------------


def load_case(run_id: str, tc_id: str) -> dict:
    """Rehydrate ONE ``TestCase`` from the run manifest.

    This is the other half of :func:`_case_dumps`: without it a resumed chat
    holds a tc_id and nothing to plan against.
    """
    try:
        from tools.models import TestCase

        manifest = (run_store.read_manifest(run_id) or {}).get("content") or {}
        for body in list(manifest.get("cases") or []):
            if not isinstance(body, dict):
                continue
            if str(body.get("tc_id") or "") != str(tc_id):
                continue
            try:
                return {"error": None, "content": TestCase(**body)}
            except Exception as exc:
                return {
                    "error": (
                        "Case "
                        + str(tc_id)
                        + " in run "
                        + str(run_id)
                        + " no longer validates ("
                        + str(exc)[:200]
                        + "), so it was not run."
                    ),
                    "content": None,
                }
        return {
            "error": (
                "Run "
                + str(run_id)
                + " has no case "
                + str(tc_id)
                + " on disk, so there is nothing to run."
            ),
            "content": None,
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.load_case failed")
        return {"error": str(exc), "content": None}


def load_run_cases(run_id: str) -> dict:
    """EVERY case of a run, rehydrated. Used by the re-run-failures source.

    Re-running the failures of a previous run needs that run's own cases, and
    the previous run is the only place they exist -- the suite it came from may
    never have been stored (a pasted markdown table is not a suite).
    """
    try:
        from tools.models import TestCase

        manifest = (run_store.read_manifest(run_id) or {}).get("content") or {}
        out: list = []
        for body in list(manifest.get("cases") or []):
            if not isinstance(body, dict):
                continue
            try:
                out.append(TestCase(**body))
            except Exception:
                logger.warning("mobile.session: case in %s no longer validates", run_id)
        if not out:
            return {
                "error": ("Run " + str(run_id) + " has no cases on disk to re-run."),
                "content": None,
            }
        return {"error": None, "content": out}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.load_run_cases failed")
        return {"error": str(exc), "content": None}


def context_for(resolved: object) -> executor.Context:
    """Build the per-call device context. Holds no tester value by default."""
    body = resolved if isinstance(resolved, dict) else {}
    # THE APP UNDER TEST, which a multi-app goal may have handed over (fix
    # round 3, item 1): `explore["app"]` is written only by
    # `_submit_explore`, after `explore_runner.handover` checked it against
    # the package the server itself saw in front. The run's own package stays
    # `home_package`, the only one `clear_app_data` may wipe. The launch
    # activity belongs to the home app, so after a hand-over it is left empty
    # and resolved from the device instead.
    explore = body.get("explore")
    explore = explore if isinstance(explore, dict) else {}
    home = str(body.get("package") or "")
    handed = str(explore.get("app") or "")
    return executor.Context(
        serial=str(body.get("serial") or ""),
        package=handed or home,
        home_package=home,
        activity="" if handed else str(body.get("activity") or ""),
        # The lane decides whether a script that asserted nothing is an
        # omission worth naming. Set HERE, at the one production construction
        # site, which both explore entry points (``next_packet`` and
        # ``submit``) already go through -- so the value cannot reach the
        # executor by one path and not the other.
        asserts_expected=str(body.get("lane") or "") != LANE_EXPLORE,
        # Per-app knowledge: the run's id and lane. ``begin_run`` reads the env
        # and app version from the run's manifest.
        run_id=str(body.get("run_id") or "") or None,
        lane=str(body.get("lane") or "") or None,
    )


def _lane_progress(run_id: str, manifest: dict, explore: dict) -> tuple[str, dict, str]:
    """``(state, scheduler point, explore stop)`` before the heartbeat check."""
    # PUBLISHED, not just branched on. This value already decided `state`
    # here while `report._is_partial` derived the same fact from the weaker
    # `explore["stop"]` and disagreed with it on the very same manifest.
    # Handing it out means every consumer reads the one producer.
    if str(manifest.get("lane") or "") == LANE_EXPLORE:
        explore_stop = explore_runner.stop_reason(explore)
        return (STATE_REPORT if explore_stop else STATE_RUNNING), {}, explore_stop
    point = (scheduler.next_case(run_id) or {}).get("content") or {}
    if point.get("finished") or run_finalize.stop_text(manifest):
        return STATE_REPORT, point, ""
    if point.get("gate"):
        return STATE_GATE, point, ""
    return STATE_RUNNING, point, ""


def _lease_age(lease: dict) -> float:
    """The lease's heartbeat age; a non-finite one reads as ZERO."""
    # A non-finite age reads as ZERO, i.e. "just refreshed", which is the
    # SAFE direction here: it leaves the run alone rather than declaring
    # somebody else's live session abandoned. Left unguarded, a NaN makes
    # the comparison in `_is_abandoned` False and the run could never be
    # reclaimed at all, because every comparison against NaN is False.
    age = float(lease.get("age") or 0.0)
    return age if math.isfinite(age) else 0.0


def _is_abandoned(state: str, lease: dict, lease_age: float) -> bool:
    """An unfinished run whose heartbeat stopped."""
    # D7 (2026-09-03): an unfinished run whose heartbeat stopped. Two runs
    # from the audit -- mrun-20260903-103316-b09ee4 (TC-003 stuck in
    # "planning", TC-004 never started) and ...103359-5b01fc -- still held
    # a lease, had no report, and `qa_mobile_status` reported them as
    # plain "running", so a tester had no way to tell a run that was
    # thinking from one nothing was driving. The lease's own heartbeat age
    # is the signal; STALE is the same threshold a takeover uses, so the
    # state and the takeover rule cannot drift apart.
    return (
        state not in (STATE_REPORT, STATE_GATE)
        and str(lease.get("state") or run_store.NONE) != run_store.NONE
        and lease_age > run_store.LEASE_STALE_S
    )


def _resolve_body(run_id: str, manifest: dict, lease: dict) -> dict:
    """The ``content`` of a resolved run, from its manifest and lease."""
    explore = manifest.get("explore")
    explore = explore if isinstance(explore, dict) else {}
    state, point, explore_stop = _lane_progress(run_id, manifest, explore)
    lease_age = _lease_age(lease)
    if _is_abandoned(state, lease, lease_age):
        state = STATE_ABANDONED
    is_explore = str(manifest.get("lane") or "") == LANE_EXPLORE
    return {
        "run_id": str(run_id),
        "state": state,
        "lane": str(manifest.get("lane") or LANE_SUITE),
        "package": str(manifest.get("package") or ""),
        "serial": str(manifest.get("serial") or ""),
        "device": device_record(manifest.get("device")),
        "avd": str(manifest.get("avd") or ""),
        "source": str(manifest.get("source") or ""),
        "total": int(manifest.get("total") or 0),
        "done": int(point.get("done") or 0),
        "failed": list(point.get("failed") or []),
        "next_tc_id": str(point.get("tc_id") or ""),
        "gate": bool(point.get("gate")),
        # The explore lane has no scheduler and therefore no `point`,
        # so this key was False for the whole life of every exploratory
        # run -- which is why a stopped run's summary was headed
        # "Mobile run progress" and its report offered "ask again later
        # for the rest". Its producer is `stop_reason`.
        "finished": bool(explore_stop) if is_explore else bool(point.get("finished")),
        "explore_stop": explore_stop,
        "final": run_finalize.recorded(manifest),
        "explore": explore,
        "holder": str(lease.get("holder") or ""),
        "lease_state": str(lease.get("state") or run_store.NONE),
        "lease_age": lease_age,
    }


def resolve(run_id: str, session_token: str = "") -> dict:
    """Where this run is, read from disk ONLY.

    Nothing about this function's answer depends on anything a previous call
    left in memory, which is what makes every state re-entrant from a fresh
    chat given only a ``run_id``.
    """
    try:
        if not run_store.valid_run_id(run_id):
            return {
                "error": "Refusing " + repr(str(run_id)[:40]) + " as a run id.",
                "content": None,
            }
        manifest = (run_store.read_manifest(run_id) or {}).get("content")
        if not isinstance(manifest, dict) or not manifest:
            return {
                "error": (
                    "No run `"
                    + str(run_id)
                    + "` on this machine. `qa_mobile_status` with no run id "
                    "lists the runs that do exist."
                ),
                "content": None,
            }
        lease = (run_store.lease_status(run_id, str(session_token or "-")) or {}).get(
            "content"
        ) or {}
        return {"error": None, "content": _resolve_body(run_id, manifest, lease)}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.resolve failed")
        return {"error": str(exc), "content": None}


def summary(run_id: str) -> dict:
    """Every checkpoint body for a run, newest verdicts included."""
    return run_store.list_cases(run_id)


def coverage_line(resolved: object, cases: object) -> str:
    """The ONE verdict-coverage sentence for a resolved run. Never raises.

    Thin on purpose. The producer is ``run_store.verdict_coverage`` /
    ``coverage_phrase`` -- the same pair ``report.py`` renders from -- and this
    is only the seam that lets the chat surface reach it without ``render.py``
    importing a store: that module imports nothing internal, and the two blocks
    of one status reply must print the SAME string rather than two counts that
    disagree about whether a status may stand in for a verdict.
    """
    try:
        body = resolved if isinstance(resolved, dict) else {}
        return run_store.coverage_phrase(
            run_store.verdict_coverage(
                cases,
                {
                    "lane": body.get("lane"),
                    "total": body.get("total"),
                    "explore": body.get("explore"),
                },
            )
        )
    except Exception:  # pragma: no cover - defensive
        logger.warning("mobile.session: could not summarise verdict coverage")
        return ""


def list_runs(limit: int = 10) -> dict:
    return run_store.list_runs(limit)


async def preflight_for(resolved: object) -> dict:
    """Run the full preflight for a resolved run. Returns EVERY check."""
    body = resolved if isinstance(resolved, dict) else {}
    return await preflight.check(
        str(body.get("package") or ""), str(body.get("serial") or "")
    )


#: Re-exported so a handler never imports ``locks`` itself: the pre-run phase
#: has no ``run_id`` to own the device with, so it holds the lock under this
#: per-process label and :func:`relabel_device_lock` hands it to the run.
def new_provisioning_owner() -> str:
    """A fresh pre-run owner label. See :func:`locks.new_provisioning_owner`.

    Re-exported as a FUNCTION so a handler never imports ``locks`` itself and
    cannot hold a shared label by mistake: the pre-run phase has no ``run_id``
    to own the device with, so it holds under one of these until
    :func:`relabel_device_lock` hands the lock to the run.
    """
    return locks.new_provisioning_owner()


def stop_ports() -> stop_run.StopPorts:
    """The ports ``stop_run.stop`` needs, from this module's own helpers.

    UNVERIFIED: ``active_runs`` sees only this process's heartbeats. ``finalize``
    writes ``manifest["final"]`` once (idempotent), so a second stop records no
    second verdict; ``release`` frees only a device this run really holds.
    """

    def active_runs(serial=None):
        runs = [stop_run.ActiveRun(r, serial_of(r)) for r in heartbeat.active()]
        return [r for r in runs if not serial or r.serial == serial]

    return SimpleNamespace(
        active_runs=active_runs,
        stop_heartbeat=heartbeat.stop,
        finalize=_finalizer(run_finalize.STOP_TESTER),
        release=release_if_held,
    )


def idle_ports() -> lock_reaper.ReleasePorts:
    """The ports ``lock_reaper.release_if_idle`` needs to end an idle holder.

    Same providers as :func:`stop_ports`, with the verdict recorded as
    ``released_idle``: the new run, not the tester, ended the old one.
    """
    return SimpleNamespace(
        stop_heartbeat=heartbeat.stop,
        finalize=_finalizer(run_finalize.STOP_IDLE),
        release_lock=release_if_held,
    )


def last_checkpoint_ts(run_id: str) -> float | None:
    """When the newest case checkpoint of *run_id* was written, or None.

    The idle release's persisted clock for a holder this process no longer
    tracks (evicted from the reaper table, or the server restarted). Never the
    lease stamp: the heartbeat refreshes that for as long as the run lives.
    Never raises.
    """
    try:
        cases = run_store.run_path(run_id) / run_store.CASES_DIR
        stamps = [p.stat().st_mtime for p in cases.glob("TC-*.json")]
    except (OSError, ValueError):
        return None
    return max(stamps, default=None)


def awaits_tester(run_id: str) -> bool:
    """True when a case of *run_id* is paused for the tester (no verdict yet)."""
    cases = (run_store.list_cases(run_id) or {}).get("content") or []
    return any(
        c.get("status") == case_runner.NEEDS_TESTER and not c.get("verdict")
        for c in cases
    )


def run_ended(run_id: str) -> bool:
    """True when the run's manifest records a stop (tester stop or idle release)."""
    read = run_store.read_manifest(run_id).get("content")
    return bool(run_finalize.stop_text(read))


def _finalizer(stop: str):
    """A ``finalize(run_id, verdict)`` port that records ``stop`` with the verdict."""

    def finalize(run_id, verdict):
        return run_finalize.finalize(run_id, verdict, stop=stop)

    return finalize


def release_if_held(serial: str, run_id: str) -> dict:
    """Compare-and-release: free ``serial`` only if ``run_id`` holds it.

    A run that holds nothing, or holds a DIFFERENT device, releases nothing, so a
    stop or an idle release can never free the device of the run that took over.
    An empty ``serial`` means the caller does not know the device: the owner's
    own holds are released, exactly what ``release_device_lock`` does by owner.
    """
    held = locks.names_held_by(run_id)
    name = locks.device_lock_name(serial)
    if not held or (name and name not in held):
        return {"error": None, "content": {"released": False, "reason": "not held"}}
    return release_device_lock(run_id, force=True)


def serial_of(run_id: str) -> str:
    """The device a run is on, from its manifest. ``""`` when it has none.

    THE ONE PRODUCER of that fact for the locking path. Every other route to it
    -- a resolved body, a packet, a context -- reads the same manifest field, so
    this is not a second source; it is the one call the lock helpers may use
    without dragging a whole ``resolve`` through the pre-packet path.
    """
    try:
        manifest = (run_store.read_manifest(str(run_id)) or {}).get("content") or {}
        return str(manifest.get("serial") or "")
    except Exception:  # pragma: no cover - defensive; a status is not a verdict
        logger.exception("mobile.session.serial_of failed")
        return ""


def take_select_lock(owner: str) -> dict:
    """Serialise DEVICE SELECTION, which is a question about the MACHINE.

    Selection reads ``adb devices``, may adopt a foreign emulator and may spawn
    the shared default one, and it runs BEFORE any serial exists -- so it cannot
    be keyed by a device and must not be. This is the lock this lane has always
    taken at the top of a new run; what changed is that it is given back the
    moment a device has been named, instead of covering the install, the
    preflight and the run.
    """
    return locks.acquire(locks.EMULATOR_LOCK, owner=str(owner))


def release_select_lock(owner: str) -> dict:
    """Give selection back. The caller minted the label, so it IS the holder."""
    return locks.release(locks.EMULATOR_LOCK, owner=str(owner), as_holder=True)


def take_device_lock(owner: str, *, serial: str = "", lease: str = "") -> dict:
    """Hold ONE DEVICE. A refusal is CONTENT, not an error.

    *owner* is a ``run_id`` once there is one, and a label from
    :func:`new_provisioning_owner` before that. Acquisition is idempotent for
    the same owner in the same process -- it performs no syscall at all -- which
    is what lets every device-touching entry take the lock without counting who
    took it first.

    ``serial`` DEFAULTS TO THE RUN'S OWN DEVICE, and that default is the design
    rather than a convenience. Every caller that has a ``run_id`` already has
    the answer on disk; making each of them look it up would put four copies of
    "which device is this run on" in the handlers, and mirrored derivations
    drift. The pre-run phase, which has no run yet, is the one caller that names
    a serial explicitly.

    A serial that names no device is REFUSED BY NAME rather than falling back to
    a lane-wide lock: a fallback would silently re-serialise every device on the
    machine, which is the behaviour this change exists to remove, and it would
    do it invisibly.
    """
    label = str(owner or "")
    wanted = str(serial or "") or serial_of(label)
    name = locks.device_lock_name(wanted)
    if not name:
        return {
            "error": None,
            "content": {
                "acquired": False,
                "owner": label,
                "holder": "",
                "reentrant": False,
                "same_process": False,
                "reason": "no_device",
            },
        }
    took = locks.acquire(name, owner=label, lease=str(lease or ""))
    if ((took or {}).get("content") or {}).get("acquired"):
        # ONE DEVICE PER OWNER, enforced at the moment a device is taken. An
        # emulator that reboots comes back on a different serial, so a resumed
        # run can otherwise end up holding the lock of a device it is no longer
        # driving -- and nothing would ever release it, because every release
        # route asks what this owner holds and would find two answers. The
        # SELECT lock is deliberately excluded: the pre-run phase holds it
        # alongside the device it has just taken, for the one line it takes to
        # give it back.
        for other in locks.names_held_by(label):
            if other not in (name, locks.EMULATOR_LOCK):
                locks.release(other, owner=label, force=True)
    return took


def release_device_lock(
    owner: str, *, lease: str = "", as_holder: bool = False, force: bool = False
) -> dict:
    """Give the emulator back. THE HOLDER releases; there is no reaper anywhere.

    Releasing is safe to call speculatively: a lock this process does not hold,
    or holds under a different owner, is reported as ``released: False`` rather
    than taken from whoever does hold it.

    **AUTHORITY IS OPT-IN, and this default used to be the other way round.**
    ``as_holder=True`` means "I have just claimed this run's lease, so I am its
    authority" -- and while it defaulted to True, a bare call asserted that on
    the caller's behalf. The two DISPLACED branches call this at the exact point
    the handler has been told it LOST the lease, so they asserted authority they
    had just been refused, and a non-holder released the lock the CURRENT holder
    was driving under. Found by an executing release-gate review, demonstrated
    end to end through both handlers, and guarded by nothing.

    So every caller now says which authority it has:

    * a chat that holds (or held) the run's lease passes ``lease=`` and is
      checked against the lock's recorded lease;
    * the pre-run phase passes ``as_holder=True`` for the placeholder it minted
      itself, which has no lease to present.
    """
    label = str(owner or "")
    names = locks.names_held_by(label)
    if not names:
        return {"error": None, "content": {"released": False, "reason": "not held"}}
    released, refusal = _release_names(names, label, lease, as_holder, force)
    return {
        "error": None,
        "content": {"released": released, "reason": "" if released else refusal},
    }


def _release_names(
    names: list, label: str, lease: str, as_holder: bool, force: bool
) -> tuple[bool, str]:
    """Release every lock *label* holds; ``(any released, first refusal reason)``."""
    # BY OWNER, NOT BY NAME. The caller does not always know the serial -- the
    # displaced branches know only that they lost the run -- and asking them to
    # derive one would be a second producer of "which device is this run on".
    # The owner label already identifies exactly one holder, which is the
    # invariant `new_provisioning_owner` exists to keep true, so "everything
    # this label holds" is precisely this caller's own holds and nobody else's.
    released = False
    refusal = ""
    for name in names:
        body = (
            locks.release(
                name,
                owner=label,
                lease=str(lease or ""),
                as_holder=bool(as_holder) and not lease,
                force=bool(force),
            )
            or {}
        ).get("content") or {}
        if body.get("released"):
            released = True
        elif not refusal:
            refusal = str(body.get("reason") or "")
    return released, refusal


async def finish_device(owner: str, **release_kwargs) -> dict:
    """Give the tester's keyboard back, THEN give the emulator back.

    **The one chokepoint for ending a run's hold on a device.** Every
    run-teardown path in ``tools/mcp_handlers`` calls this rather than
    :func:`release_device_lock` directly, and a test pins that there is no direct
    call left in that file -- so a fifth teardown path added later cannot skip
    the restore by reaching for the lock function.

    **Why this exists instead of putting the restore inside
    :func:`release_device_lock`.** That function is synchronous and takes no
    serial, deliberately: its own comment says "BY OWNER, NOT BY NAME. The caller
    does not always know the serial". ``ime.restore_previous`` is async and needs
    one. Putting the restore inside it would have meant changing the signature of
    a function with eight callers, or driving an event loop from a sync function
    that several callers already invoke from inside one. So the lock function is
    untouched and this wraps it.

    The restore is a no-op for a run that never typed -- which is most of them,
    and all of the pre-run placeholder releases -- because
    ``ime_session.restore`` reads a record that only ``ensure_ready`` writes.

    **Not called by the capture lane or the heartbeat.** Those take the device
    lock for a certificate install, a proxy or a lease sweep; they never touched
    an input method and have nothing to give back, so they keep calling the
    synchronous function directly.

    **What this does NOT cover:** a hard crash or a killed process calls nothing
    at all -- :func:`release_device_lock` says it, and it is true here too. There
    is no reaper in this lane, so a run killed outright leaves the QA keyboard
    selected until something else runs. The doctor's per-device "selected" row is
    what surfaces that afterwards.

    **Keywords are forwarded verbatim, not restated.** ``**release_kwargs`` goes
    straight to :func:`release_device_lock`, so this wrapper holds no copy of
    that function's defaults -- a copy would pin today's values and drift the
    day one of them changed. It also means a caller that passes only ``lease=``
    still calls a one-keyword function, which is what every existing caller and
    every existing test double expects.
    """
    restores = await _restore_run_state(str(owner))
    stale = await _sweep_stale_state(str(owner))
    released = release_device_lock(str(owner), **release_kwargs)
    return _finish_device_reply(released, restores, stale)


async def _restore_run_state(owner: str) -> tuple[dict, dict, dict]:
    """Disarm, then restore keyboard, animations and a11y: ``(ime, anim, a11y)``."""
    from tools.mobile import a11y, animations, ime, ime_session

    # DISARM first (G4): the nonce is forgotten even when the device is gone.
    try:
        await ime.disarm(serial_of(owner) or "")
    except Exception:
        logger.debug("mobile.session.finish_device: disarm skipped", exc_info=True)
    restored = await ime_session.restore(owner)
    # The run's animation scales go back too, on the same chokepoint and for the
    # same reason: every teardown path reaches this function.
    anim = await animations.restore(owner)
    # The a11y service goes off and the tester's accessibility settings come back.
    a11y_restored = await a11y.restore(owner)
    return restored, anim, a11y_restored


async def _sweep_stale_state(owner: str) -> tuple[dict, dict, dict]:
    """Restore what earlier crashed runs left: ``(ime, animations, a11y)``."""
    from tools.mobile import a11y, animations, ime_session

    a11y_stale = {}
    anim_stale = {}
    # AND ANY KEYBOARD AN EARLIER RUN CRASHED OUT OF. Done here as well as at
    # run start because the two reach different machines: run start covers the
    # tester who runs the lane again, this covers the run that is ending on a
    # device whose previous holder died. Both go through the same function, so
    # neither can drift from the held-device check that makes it safe.
    swept = {}
    try:
        # `serial_of` is THE producer of a run's device for the locking path --
        # its own docstring says so -- rather than a second derivation off the
        # restore envelope, which does not carry one.
        serial = serial_of(str(owner))
        if serial:
            swept = (
                await ime_session.restore_stale(serial, skip_run_id=str(owner)) or {}
            ).get("content") or {}
            anim_stale = (
                await animations.restore_stale(serial, skip_run_id=str(owner)) or {}
            ).get("content") or {}
            a11y_stale = (
                await a11y.restore_stale(serial, skip_run_id=str(owner)) or {}
            ).get("content") or {}
    except Exception:
        logger.debug("mobile.session.finish_device: stale sweep skipped", exc_info=True)
    return swept, anim_stale, a11y_stale


def _finish_device_reply(released: dict, restores: tuple, stale: tuple) -> dict:
    """The ``finish_device`` envelope from the release result and both restores."""
    restored, anim, a11y_restored = restores
    swept, anim_stale, a11y_stale = stale
    return {
        "error": None,
        "content": {
            "released": bool((released.get("content") or {}).get("released")),
            "release": released.get("content") or {},
            # Carried rather than swallowed: a restore that failed is a device
            # left on the QA keyboard, and the reply a tester reads is the only
            # place that can say so.
            "ime": restored.get("content")
            or {"restored": False, "detail": str(restored.get("error") or "")},
            # A SECOND VALUE WITH A SECOND NAME, not a caveat on the first.
            # "the keyboard this run displaced" and "a keyboard an earlier,
            # crashed run displaced" are different facts with different owners,
            # and a reader that had to tell them apart from one field would be
            # guessing.
            "ime_stale": swept,
            "animations": anim.get("content")
            or {"restored": False, "detail": str(anim.get("error") or "")},
            "animations_stale": anim_stale,
            "a11y": a11y_restored.get("content")
            or {"restored": False, "detail": str(a11y_restored.get("error") or "")},
            "a11y_stale": a11y_stale,
        },
    }


def relabel_device_lock(from_owner: str, to_owner: str) -> dict:
    """Hand the device from the provisioning phase to the run that now owns it.

    The SAME file descriptor keeps the SAME kernel lock. Deliberately not
    release-then-reacquire, which would open a window in which another process
    could take the device between the two.
    """
    old = str(from_owner or "")
    # THE SELECT LOCK IS NEVER HANDED TO A RUN. It is the machine's, not the
    # run's, and a run that inherited it would serialise every other device on
    # the machine for its whole life. Releasing it here rather than trusting the
    # caller to have done it keeps the hand-off total: after this call the
    # pre-run label holds nothing, whatever path reached it.
    if locks.EMULATOR_LOCK in locks.names_held_by(old):
        locks.release(locks.EMULATOR_LOCK, owner=old, as_holder=True)
    names = [n for n in locks.names_held_by(old) if n != locks.EMULATOR_LOCK]
    if not names:
        return {
            "error": None,
            "content": {"relabelled": False, "reason": "not held", "holder": ""},
        }
    for name in names:
        moved = locks.relabel(name, from_owner=old, to_owner=str(to_owner))
        if not ((moved or {}).get("content") or {}).get("relabelled"):
            return moved
    return {"error": None, "content": {"relabelled": True, "reason": "", "holder": ""}}


#: ``locale.source`` -- the one sentinel this module invents for the language
#: fact, with exactly three values. Consumers: the manifest written by the two
#: planners, ``report._locale_cell`` (which branches on all three) and
#: :func:`locale_phrase` (which deliberately does NOT branch on it -- a tester
#: reading the chat line needs the language and whether it took, not how it got
#: there, and that is the right ``else`` rather than an oversight).
LOCALE_FROM_BOOT = "boot"
LOCALE_FROM_SETPROP = "setprop"
LOCALE_FROM_DEVICE = "device"


def _same_language(actual: object, wanted: object) -> bool:
    """Does *actual* satisfy the request *wanted*?

    A request WITH a region must match exactly: a tester who asked for ``ar-EG``
    and got ``ar-SA`` has different date, number and currency formats, which is
    frequently the thing under test. A request without one is satisfied by any
    region of that language. Empty on either side is never a match -- an unknown
    locale is not a satisfied one.
    """
    got = str(actual or "").strip()
    ask = str(wanted or "").strip()
    if not got or not ask:
        return False
    if "-" in ask:
        return got == ask
    return got.split("-")[0] == ask


def _locale_body(
    requested: object, actual: object, persisted: object, source: object, detail: object
) -> dict:
    """THE shape of the language record, built in one place.

    ``matched`` is a POSITIVE claim made only from a read-back that answered:
    an unanswerable probe leaves it False, so no consumer can turn "we could not
    ask" into "the run was in Arabic". Same rule, same reason, as
    ``install_state.probed``.
    """
    return {
        "requested": str(requested or ""),
        "actual": str(actual or ""),
        "persisted": str(persisted or ""),
        "source": str(source or LOCALE_FROM_DEVICE),
        "matched": (
            _same_language(actual, requested) if requested else bool(str(actual or ""))
        ),
        "detail": str(detail or "")[:200],
    }


def locale_record(body: object) -> dict:
    """Normalise a language record for the manifest. Never raises.

    The counterpart of :func:`device_record`: one shape written by the two
    planners and read by every consumer, so a reader never has to ask which of
    two spellings a manifest used.
    """
    given = body if isinstance(body, dict) else {}
    return _locale_body(
        given.get("requested"),
        given.get("actual"),
        given.get("persisted"),
        given.get("source"),
        given.get("detail"),
    )


async def apply_locale(
    serial: str, requested: str = "", *, booted_with: str = ""
) -> dict:
    """Put the device into *requested* and report what it is ACTUALLY in.

    ``{"error", "content": {requested, actual, persisted, source, matched, detail}}``

    Three outcomes, and they are deliberately not one:

    * nothing requested -> one read, and the record says what the device was
      already in (``source=device``);
    * requested and the device is already in it -> confirmed, no write;
    * requested and not -> ``adb.set_locale`` is attempted and the answer is
      whatever the device says afterwards, never the exit code.

    This function never refuses; it REPORTS. The refusal is the caller's, in
    ``mcp_handlers._mobile_locale_stage``, because only the caller knows whether
    a run is about to be started on the strength of it.
    """
    try:
        content = await _locale_content(serial, requested, booted_with)
        return {"error": None, "content": content}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.apply_locale failed")
        return {"error": str(exc), "content": None}


async def _locale_content(serial: str, requested: str, booted_with: str) -> dict:
    """The language record for one ``apply_locale`` call (may raise)."""
    wanted = str(requested or "").strip()
    seen = await adb.runtime_locale(serial)
    if seen.get("error"):
        return _locale_body(
            wanted, "", "", LOCALE_FROM_DEVICE, str(seen["error"])[:200]
        )
    actual = str(seen.get("content") or "")
    if not wanted:
        return _locale_body("", actual, "", LOCALE_FROM_DEVICE, "")
    source = (
        LOCALE_FROM_BOOT
        if str(booted_with or "").strip() == wanted
        else LOCALE_FROM_SETPROP
    )
    if _same_language(actual, wanted):
        persisted = await adb.persisted_locale(serial)
        saved = "" if persisted.get("error") else str(persisted.get("content") or "")
        return _locale_body(wanted, actual, saved, source, "")
    return await _locale_changed(serial, wanted, actual, source)


async def _locale_changed(serial: str, wanted: str, actual: str, source: str) -> dict:
    """Write *wanted* to the device and report what it says afterwards."""
    changed = await adb.set_locale(serial, wanted)
    if changed.get("error"):
        return _locale_body(wanted, actual, "", source, str(changed["error"])[:200])
    body = changed.get("content") or {}
    return _locale_body(
        wanted,
        str(body.get("runtime") or ""),
        str(body.get("persisted") or ""),
        source,
        "",
    )


def locale_phrase(resolved: object) -> str:
    """The language clause of the device line, or ``""``.

    Read from the RECORD, never from the device again: two readers asking the
    device could get two answers, and a finished run must still say what it ran
    in after the emulator is gone.
    """
    body = resolved if isinstance(resolved, dict) else {}
    record = body.get("locale") if isinstance(body.get("locale"), dict) else {}
    actual = str(record.get("actual") or "")
    requested = str(record.get("requested") or "")
    if not actual and not requested:
        return ""
    if requested and not record.get("matched"):
        return ", NOT in " + requested + (" but in " + actual if actual else "")
    return ", in " + actual if actual else ""


def capture_record(source: object) -> dict:
    """Normalise the run-level API-capture tier for the manifest. Never raises.

    THE ONE PRODUCER of the tier vocabulary is ``tools.mobile_capture.ladder``;
    this is a thin pass-through so the two planners below need no import at
    module load time and every consumer of the manifest's ``capture`` key
    reads the SAME shape ``ladder.record`` defines -- never a second
    normaliser drifting from the first.
    """
    from tools.mobile_capture import ladder

    return ladder.record(source)


def reset_app_record(body: object) -> dict:
    """Normalise the pre-run app-reset outcome for the manifest. Never raises.

    The counterpart of :func:`locale_record`/:func:`capture_record`: one
    shape written by both planners below and read by every consumer, so a
    finished run still states whether its own app's data was wiped before it
    started, after the device that ran ``pm clear`` is gone.
    """
    given = body if isinstance(body, dict) else {}
    return {
        "requested": bool(given.get("requested")),
        "package": str(given.get("package") or ""),
        "cleared": bool(given.get("cleared")),
    }


def reset_step_status(resolved: object, cases: object) -> dict:
    """What "reset" means for the relay block: requested from EITHER the
    launch-time ``reset_app=true`` flag (the manifest's ``reset_app`` record,
    see :func:`reset_app_record`) OR a ``clear_app_data`` op the model
    submitted mid-run, and done only when a clear actually landed. A
    requested reset that never confirmed is exactly the gap the relay block
    exists to surface. A run once read as a pass after a reset that never ran.
    """
    body = resolved if isinstance(resolved, dict) else {}
    manifest_reset = body.get("reset_app")
    manifest_reset = manifest_reset if isinstance(manifest_reset, dict) else {}
    requested = bool(manifest_reset.get("requested"))
    done = bool(manifest_reset.get("cleared"))
    for row in list(cases or []):
        if not isinstance(row, dict):
            continue
        for item in list(row.get("trace") or []):
            if not isinstance(item, dict):
                continue
            action = item.get("action") if isinstance(item.get("action"), dict) else {}
            if str(action.get("op") or "") != "clear_app_data":
                continue
            requested = True
            if str(item.get("outcome") or "") == "ok":
                done = True
    return {"requested": requested, "done": done}


def run_verdict(resolved: object, cases: object) -> str:
    """pass / fail / unverified -- the ONE word a tester reads to answer "did
    this run pass". An abandoned run and a requested-but-unconfirmed reset
    BOTH force ``unverified``: a run this lane cannot vouch for must never
    read as a pass, which is the defect a chat once told a tester "Login
    succeeded" against.
    """
    from tools.mobile import case_runner

    body = resolved if isinstance(resolved, dict) else {}
    rows = [row for row in list(cases or []) if isinstance(row, dict)]
    reset = reset_step_status(body, rows)
    if str(body.get("state") or "") == STATE_ABANDONED:
        return case_runner.VERDICT_UNVERIFIED
    if reset.get("requested") and not reset.get("done"):
        return case_runner.VERDICT_UNVERIFIED
    if not rows:
        return case_runner.VERDICT_UNVERIFIED
    verdicts = {str(row.get("verdict") or "") for row in rows}
    if case_runner.VERDICT_FAIL in verdicts:
        return case_runner.VERDICT_FAIL
    if verdicts <= {case_runner.VERDICT_PASS}:
        return case_runner.VERDICT_PASS
    return case_runner.VERDICT_UNVERIFIED


def device_record(facts: object) -> dict:
    """Normalise ``adb.device_facts`` content for the manifest. Never raises.

    One shape, written by the two planners and read by every consumer, so a
    reader never has to ask which of two spellings a manifest used. An unknown
    kind stays ``""`` rather than defaulting to ``emulator``: guessing here
    would put a claim about the EVIDENCE VALUE of a run into the report, and a
    confident wrong answer is worse than a blank.
    """
    body = facts if isinstance(facts, dict) else {}
    kind = str(body.get("kind") or "")
    return {
        "kind": kind if kind in ("emulator", "physical") else "",
        "model": str(body.get("model") or "")[:60],
        "api": str(body.get("api") or "")[:8],
    }


def device_line(resolved: object) -> str:
    """The tester-facing "what hardware was this?" line, or ``""``.

    ``""`` for a run with no SERIAL, and only for that: an empty line is the
    honest answer when the record does not say which device, and the alternative
    ("emulator", assumed) would be a claim about evidence nobody made.

    A run WITH a serial and no recorded ``kind`` -- every manifest written before
    that field existed -- takes its own branch below and renders
    "(kind not recorded for this run)". So this function does NOT render an older
    run exactly as it did before ``device.kind``, and the claim that it does was
    left standing here after the report started naming the same state: see
    ``report.DEVICE_KIND_UNKNOWN``, which is a SECOND wording of this one. The
    ``else "a physical device"`` below therefore cannot be reached by an
    unrecognised kind, because ``device_record`` writes ``""`` for one and that
    empty kind is handled above.
    """
    body = resolved if isinstance(resolved, dict) else {}
    serial = str(body.get("serial") or "")
    if not serial:
        return ""
    device = body.get("device") if isinstance(body.get("device"), dict) else {}
    kind = str(device.get("kind") or "")
    if not kind:
        return (
            "\n\n- Device: `"
            + serial
            + "` (kind not recorded for this run)"
            + locale_phrase(body)
        )
    detail = ", ".join(
        part
        for part in (
            str(device.get("model") or ""),
            ("API " + str(device.get("api"))) if device.get("api") else "",
        )
        if part
    )
    return (
        "\n\n- Device: `"
        + serial
        + "` — "
        + ("an emulator" if kind == "emulator" else "a physical device")
        + ((" (" + detail + ")") if detail else "")
        + locale_phrase(body)
    )


# ---------------------------------------------------------------------------
# Producing and consuming packets
# ---------------------------------------------------------------------------


async def next_packet(
    run_id: str,
    session_token: str = "",
    *,
    past_gate: bool = False,
    budget: Budget | None = None,
) -> dict:
    """The next thing the tester's model must answer, or a terminal state.

    ``{"error", "content": {"state", "packet", "tc_id", "resolved", ...}}``.

    ``past_gate`` is the tester having ALREADY answered the soft "keep going?"
    gate. It belongs here, at the one place the gate state is decided, because
    the gate was previously evaluated twice -- once in the handler, which
    honoured the override, and again in the renderer, which did not -- so a run
    past the gate looped on it forever. One decision, one answer.
    """
    try:
        resolved = resolve(run_id, session_token)
        if resolved.get("error"):
            return resolved
        body = resolved["content"] or {}
        ctx = context_for(body)
        if str(body.get("lane")) == LANE_EXPLORE:
            return await _next_explore_packet(run_id, body, ctx, budget)
        return await _next_suite_packet(run_id, body, ctx, past_gate, budget)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.next_packet failed")
        return {"error": str(exc), "content": None}


async def _next_explore_packet(
    run_id: str, body: dict, ctx: executor.Context, budget: Budget | None
) -> dict:
    """The explore lane's next packet (one turn), or its busy/report answer."""
    state = body.get("explore") or {}
    if budget is not None and _in_reserve(budget):
        # Checked BEFORE the turn, never during: a turn dumps the screen
        # and writes state, and abandoning one half-done is worse than
        # answering with a pointer. The reserved tail is spent on ONE
        # best-effort capture instead of returning bare -- see
        # _final_packet.
        # Always attempt the capture: _final_packet self-guards with
        # asyncio.wait_for(..., timeout=max(0.0, remaining())), so an
        # already-expired budget degrades to None on its own instead
        # of skipping the attempt outright.
        return _busy(body, "", packet=await _final_packet(ctx, budget))
    turn = await explore_runner.next_turn(run_id, state, ctx)
    if turn.get("error"):
        return turn
    content = turn.get("content") or {}
    running = str(content.get("status") or "") == explore_runner.RUNNING
    if running:
        # WHEN THIS TURN BEGAN, in the same unit the suite lane uses for
        # a case. The checkpoint is written when the turn ENDS, so
        # without this the only instant it knows is the end and its
        # evidence window collapses to the pad around it.
        began = dict(content.get("state") or {})
        began["turn_started"] = time.time()
        content["state"] = began
    content["state"] = await _explore_turn_network(
        run_id, content, ctx, running=running, budget=budget
    )
    _persist_explore(run_id, content.get("state"))
    return {
        "error": None,
        "content": {
            "state": STATE_RUNNING if running else STATE_REPORT,
            "packet": content.get("packet"),
            "tc_id": "",
            "status": str(content.get("status") or ""),
            "resolved": body,
        },
    }


async def _explore_turn_network(
    run_id: str,
    content: dict,
    ctx: executor.Context,
    *,
    running: bool,
    budget: Budget | None,
) -> object:
    """The turn's state after starting (or, once stopped, aborting) its capture."""
    # The wire, for this turn. Started HERE, at the packet build, for
    # the same reason the suite lane starts it before its launch: the
    # capture has to cover what the turn is about to do. A stopped
    # session, a physical device or a flag that is off all land as a
    # record which SAYS so, and none of them can block a turn.
    # The budget is re-read HERE and not only before the turn: the
    # capture adds two device round trips AFTER the only earlier check,
    # and on a slow emulator that is what pushes the packet build past
    # the client's timeout. Losing the capture costs a section that
    # says why; losing the call costs the turn.
    if running and not (budget is not None and budget.expired()):
        return await _explore_begin_network(run_id, content.get("state"), ctx)
    if not running:
        # The session has STOPPED. Any capture a previous packet build
        # started is now never going to be read, so it is stopped and
        # its file deleted here rather than left recording the tester's
        # device until something else displaces it.
        return await _explore_abort_network(run_id, content.get("state"), ctx)
    return content.get("state")


async def _next_suite_packet(
    run_id: str,
    body: dict,
    ctx: executor.Context,
    past_gate: bool,
    budget: Budget | None,
) -> dict:
    """The suite lane's next packet: report, gate, busy or a started case."""
    if body.get("finished"):
        await _finish_evidence(run_id, body)
        return {
            "error": None,
            "content": {
                "state": STATE_REPORT,
                "packet": None,
                "tc_id": "",
                "resolved": body,
            },
        }
    if body.get("gate") and not past_gate:
        return {
            "error": None,
            "content": {
                "state": STATE_GATE,
                "packet": None,
                "tc_id": str(body.get("next_tc_id") or ""),
                "resolved": body,
            },
        }
    loaded = load_case(run_id, str(body.get("next_tc_id") or ""))
    if loaded.get("error"):
        return loaded
    if budget is not None and _in_reserve(budget):
        # start_case force-stops the app, relaunches it and dumps the screen.
        # With the budget gone that work would land after the client has
        # already given up on the call, so nothing is started at all. The
        # reserved tail is spent on ONE best-effort capture instead -- see
        # _final_packet.
        # Always attempt the capture -- see the matching comment in the
        # explore-lane busy branch above.
        return _busy(
            body,
            str(body.get("next_tc_id") or ""),
            packet=await _final_packet(ctx, budget),
        )
    started = await case_runner.start_case(run_id, loaded["content"], ctx)
    if started.get("error"):
        return started
    content = started.get("content") or {}
    return {
        "error": None,
        "content": {
            "state": STATE_RUNNING,
            "packet": content.get("packet"),
            "tc_id": str(content.get("tc_id") or ""),
            "resolved": body,
        },
    }


async def _finish_evidence(run_id: str, resolved: object) -> None:
    """Pull the app's own event log ONCE, when a run reaches its report.

    Idempotent on the manifest: a run whose manifest already records an
    ``events_source`` is not pulled again, so a resumed chat that lands on the
    finished state a second time makes no adb call. ``capture.pull_events``
    itself skips, with a reason, when the package has no profile, the flag is
    off, or ``run-as`` is refused (a release build) -- and whatever it says is
    recorded so the report can show it (plan D8). Nothing here can change the
    run's state or a verdict: every exception ends in this function.
    """
    try:
        body = resolved if isinstance(resolved, dict) else {}
        manifest = (run_store.read_manifest(run_id) or {}).get("content") or {}
        if not isinstance(manifest, dict) or manifest.get("events_source"):
            return
        pulled = await capture.pull_events(
            str(body.get("serial") or manifest.get("serial") or ""),
            str(body.get("package") or manifest.get("package") or ""),
            run_id,
        )
        content = pulled.get("content") if isinstance(pulled, dict) else None
        content = content if isinstance(content, dict) else {}
        # RE-READ after the await: a state or lease write may have landed while
        # the device was being read, and the copy taken before the pull would
        # roll it back. Only the three evidence keys are merged into the fresh copy.
        fresh = (run_store.read_manifest(run_id) or {}).get("content")
        fresh = fresh if isinstance(fresh, dict) else manifest
        fresh["events_source"] = str(content.get("events_source") or "none")
        fresh["events_reason"] = str(
            content.get("reason")
            or (pulled.get("error") if isinstance(pulled, dict) else "")
            or ""
        )[:400]
        fresh["events_segments"] = content.get("segments")
        run_store.write_manifest(run_id, fresh)
    except Exception:  # never-raise: evidence is not a verdict
        logger.exception("mobile.session._finish_evidence failed")


async def finish_evidence(run_id: str, resolved: object) -> None:
    """The public face of :func:`_finish_evidence`, for callers outside this module.

    ``qa_mobile_status(report_now=True)`` renders a run's report from another chat
    and must pull the app's event log first, once; it goes through here so that a
    module boundary is crossed by a public name, not a private one. Same contract:
    idempotent on the manifest, never raises, never changes state or a verdict.
    """
    await _finish_evidence(run_id, resolved)


#: What an exploratory turn is checkpointed as. An explore run used to write NO
#: case record at all: ``_submit_explore`` replayed the turn, folded the reply
#: and returned, while ``tools/mobile/report.py`` is case-driven from
#: ``cases/TC-*.json`` -- so a real four-turn run
#: (``mrun-20260904-194622-bd5142``) produced a 147 KB report reading "0 cases
#: checkpointed" and "No case has been checkpointed yet". A turn IS the unit of
#: work in this lane, so it is checkpointed as one, through the same
#: ``run_store`` path the suite lane uses. That is why the existing report
#: machinery draws it with no change to what a "case" means on disk.
EXPLORE_MODULE = "Exploration"
EXPLORE_TITLE_PREFIX = "Exploratory turn "


def explore_turn_tc_id(turn: object) -> str:
    """The synthetic, STABLE case id for one exploratory turn.

    Stable is the load-bearing word: the same turn submitted twice (a refused
    script, a resumed chat) must reuse its id, or the report grows a duplicate
    card per retry. The turn number is the only identifier a turn has that
    survives an MCP restart, and ``run_store.valid_tc_id`` already accepts
    exactly this shape -- so no store contract moves for this fix.
    """
    try:
        number = int(turn or 0)
    except (TypeError, ValueError, OverflowError):
        number = 0
    return "TC-%03d" % max(1, min(number, 999999))


def explore_turn_number(state: object) -> int:
    """THE turn number an exploratory state is on. One coercion, one place.

    The checkpoint needs the NUMBER (it titles the card with it) and the
    recorder needs the ID; both must agree, so the coercion that turns a junk
    field into a usable number lives here and nowhere else.
    """
    body = state if isinstance(state, dict) else {}
    try:
        return max(1, int(body.get("turn") or 1))
    except (TypeError, ValueError, OverflowError):
        return 1


def explore_turn_case_id(state: object) -> str:
    """THE case id one exploratory turn writes, derived from its STATE.

    ONE producer, because two callers need the same answer at two moments:
    ``_checkpoint_explore_turn`` WRITES the turn's case under this id, and the
    recorder around the replay must file its clip onto that same case -- and it
    needs the id BEFORE the checkpoint exists. Deriving the number twice is how
    a clip lands on a case id nothing else uses, which is silent: the report
    finds no media on the case it renders and draws the old wireframe.
    """
    return explore_turn_tc_id(explore_turn_number(state))


def _latest_finding(state: object) -> str:
    """The finding recorded for the CURRENT turn, or ``""``.

    Matched on the turn number rather than taken as the last entry: findings
    are appended only when a turn reports one, so "the last note" would make a
    silent turn inherit the previous turn's finding and report evidence that
    turn never produced.
    """
    body = state if isinstance(state, dict) else {}
    try:
        turn = int(body.get("turn") or 0)
    except (TypeError, ValueError, OverflowError):
        return ""
    notes = [f for f in list(body.get("findings") or []) if isinstance(f, dict)]
    for entry in reversed(notes):
        try:
            if int(entry.get("turn") or 0) == turn:
                return str(entry.get("note") or "")
        except (TypeError, ValueError, OverflowError):
            continue
    return ""


def _explore_prior_verified(run_id: str) -> bool:
    """True when an EARLIER turn of this run recorded an ``assert_pass``.

    The explore lane checkpoints one turn as one case, so the turn that calls
    ``done`` has a trace of its own that can never carry the check an earlier
    turn made. The unit that earns a verdict here is the RUN -- the goal -- so
    the evidence is read run-wide and handed to the executor as an explicit
    input (``executor.Context.prior_verified``). The executor still knows
    nothing about runs, and the scripted lane is untouched because that field
    defaults to False.

    The OUTCOME is what counts, not the presence of an assert: a run whose only
    check FAILED has verified nothing.

    Never raises: an unreadable case store is "no prior evidence".
    """
    try:
        listed = run_store.list_cases(run_id) or {}
        for body in listed.get("content") or []:
            if not isinstance(body, dict):
                continue
            for item in list(body.get("trace") or []):
                if (
                    isinstance(item, dict)
                    and str(item.get("outcome") or "") == "assert_pass"
                ):
                    return True
    except Exception:  # pragma: no cover - defensive
        logger.warning("mobile.session: could not read this run's prior evidence")
    return False


def _explore_prior_turn_blocked(run_id: str) -> bool:
    """True when the LATEST checkpointed turn failed to type or moved nothing.

    ONE turn, not run-wide like :func:`_explore_prior_verified`: a failure
    several turns back must not block every later pass once the app moved on.
    "Latest" is by the turn number in ``tc_id``, not list position. Counted: a
    reason carrying :data:`executor.NOTHING_MOVED_PREFIX`, or a ``type``/
    ``clear`` whose outcome was ``device_error`` or ``refused``.

    Never raises: an unreadable case store is "not blocked".
    """
    try:
        listed = run_store.list_cases(run_id) or {}
        latest: dict | None = None
        latest_turn = -1
        for body in listed.get("content") or []:
            if not isinstance(body, dict):
                continue
            digits = "".join(ch for ch in str(body.get("tc_id") or "") if ch.isdigit())
            turn = int(digits) if digits else -1
            if turn >= latest_turn:
                latest, latest_turn = body, turn
        if latest is None:
            return False
        if executor.NOTHING_MOVED_PREFIX in str(latest.get("reason") or ""):
            return True
        for item in list(latest.get("trace") or []):
            if not isinstance(item, dict):
                continue
            action = item.get("action") if isinstance(item.get("action"), dict) else {}
            if str(action.get("op") or "") in ("type", "fill", "clear") and str(
                item.get("outcome") or ""
            ) in ("device_error", "refused"):
                return True
    except Exception:  # pragma: no cover - defensive
        logger.warning("mobile.session: could not read this run's previous turn")
    return False


def _prior_guard_stop_explore(run_id: str, tc_id: str) -> dict:
    """The explore lane's own guard-stop lookup: an EXACT match on ``tc_id``
    first (the common case, a resubmission of the SAME turn), and only when
    that misses, the highest-turn checkpointed case's own ``guard_stop`` --
    verbatim, so a confirm still spends against exactly what that turn
    recorded (``{}`` if that turn recorded none, which keeps the one-shot
    confirm inert rather than manufacturing a stop for it to consume).

    This exists because the explore lane's turn counter
    (``explore_runner.next_packet``) bumps ``state["turn"]`` on EVERY call,
    not only across a resubmission of the same turn -- so a confirm
    submitted after the scheduler already advanced past this turn names a
    ``tc_id`` no case was ever checkpointed under, and the exact-match lookup
    alone returns ``{}`` and the guard fires again on a turn that already
    confirmed. The scripted lane never reaches this: it always has an exact
    ``tc_id`` match once a case exists, so :func:`_prior_guard_stop` alone
    still serves it directly. FAILS CLOSED the same way its sibling does:
    any read that raises, or no checkpointed case at all, is untracked --
    ``{}``.
    """
    exact = _prior_guard_stop(run_id, tc_id)
    if exact:
        return exact
    try:
        listed = run_store.list_cases(run_id) or {}
        rows = [b for b in (listed.get("content") or []) if isinstance(b, dict)]
        if not rows:
            return {}
        # (length, text): TC-\d{3,6} sorts numerically, so TC-1000 > TC-999.
        latest = max(
            rows,
            key=lambda b: (len(str(b.get("tc_id") or "")), str(b.get("tc_id") or "")),
        )
        stop = latest.get("guard_stop")
        return dict(stop) if isinstance(stop, dict) else {}
    except Exception:  # pragma: no cover - defensive
        logger.warning(
            "mobile.session: could not read the latest explore turn's guard stop"
        )
        return {}


def _prior_guard_stop(run_id: str, tc_id: str) -> dict:
    """THIS case's own last recorded guard-stop fingerprint, or ``{}``.

    Scoped to the ONE case being submitted -- an exact match on ``tc_id`` --
    not a run-wide "latest by tc_id digit" scan: that scan's "latest" case
    can be a DIFFERENT tc_id than the one this confirm names, which would let
    a stop recorded against one case authorise a confirm submitted for
    another. The scripted lane passes its own ``tc_id`` and stops here. The
    explore lane no longer calls this function directly -- its turn counter
    advances on EVERY packet, not only across a resubmission, so an exact
    match alone misses a confirm submitted after the scheduler moved on; see
    :func:`_prior_guard_stop_explore`, which tries this exact match FIRST
    and only then falls back.

    FAILS CLOSED: any read that raises, or a matching case that recorded no
    stop, is untracked -- ``{}`` -- rather than treated as leniently as
    :func:`_explore_prior_turn_blocked` treats its own question. That
    sibling fails OPEN because its wrong "no" only costs one extra retry;
    this one's wrong "no" would let a mismatched confirm through, which is
    the defect this function exists to close.
    """
    try:
        listed = run_store.list_cases(run_id) or {}
        for body in listed.get("content") or []:
            if isinstance(body, dict) and str(body.get("tc_id") or "") == str(tc_id):
                stop = body.get("guard_stop")
                return dict(stop) if isinstance(stop, dict) else {}
    except Exception:  # pragma: no cover - defensive
        logger.warning("mobile.session: could not read this case's last guard stop")
    return {}


def _explore_planned_case(tc_id: str, state: object, finding: str = "") -> dict:
    """The turn as a MINIMAL valid ``TestCase`` dump, for ``manifest["cases"]``.

    Valid rather than ad-hoc because :func:`load_case` and
    :func:`load_run_cases` rehydrate every entry of that list through
    ``TestCase(**body)``: a shape that failed validation would be dropped with
    a warning, and the re-run-failures source would silently lose the run. It
    is also what lets the report's ``_planned_case`` print what the turn was
    FOR.
    """
    body = state if isinstance(state, dict) else {}
    goal = " ".join(str(body.get("goal") or "").split())[:400] or "not recorded"
    try:
        turn = max(1, int(body.get("turn") or 1))
    except (TypeError, ValueError, OverflowError):
        turn = 1
    note = " ".join(str(finding or "").split())[:400]
    return {
        "tc_id": tc_id,
        "module": EXPLORE_MODULE,
        "title": (EXPLORE_TITLE_PREFIX + str(turn) + " \u2014 " + goal)[:250],
        "priority": "Medium",
        "type": "Exploratory",
        "preconditions": "The app was open at the previous turn's screen.",
        "steps": [
            {
                "step_number": 1,
                "action": "Explore towards the goal: " + goal,
                "expected_result": (
                    "Recorded finding: " + note
                    if note
                    else (
                        "An exploratory turn has no expected result; it records "
                        "what was found."
                    )
                ),
            }
        ],
        "automation_status": "Manual",
    }


def _checkpoint_explore_turn(
    run_id: str,
    state: object,
    outcome: object,
    finding: str = "",
    verdict: str = "",
    network: object = None,
) -> dict:
    """Write ONE exploratory turn as a case-shaped record. Never raises.

    Best-effort exactly like ``run_store.write_screen``: a lost checkpoint may
    never lose a turn's replay, so nothing here can return an error into the
    submit path. Returns the body it wrote (or tried to), because the caller
    reports the same id and title back to the tester.

    The record carries a STATUS and, on every turn but one, no verdict. An
    exploratory turn has no expected result, so it has none to earn, and
    ``report._verdict_of`` shows the status instead -- never inventing a pass.
    The exception is a turn whose ``done()`` judged the goal: the unit that
    earns a verdict in this lane is the RUN, and the caller passes that verdict
    in so the case-driven report has one to render, in the same field the suite
    lane uses.
    """
    body = state if isinstance(state, dict) else {}
    result = outcome if isinstance(outcome, dict) else {}
    turn = explore_turn_number(body)
    tc_id = explore_turn_tc_id(turn)
    goal = " ".join(str(body.get("goal") or "").split())[:200] or "no goal recorded"
    now = time.time()
    began = _turn_began(body, now)
    written = {
        "tc_id": tc_id,
        "title": (EXPLORE_TITLE_PREFIX + str(turn) + " \u2014 " + goal)[:250],
        **_explore_result_fields(result, verdict),
        "escapes": 0,
        "guard_stop": _explore_guard_stop(result),
        "finding": " ".join(str(finding or "").split())[:600],
        "started": min(began, now),
        "updated": now,
        "evidence": _explore_turn_evidence(network),
    }
    try:
        run_store.write_case(run_id, tc_id, written)
    except Exception:  # pragma: no cover - defensive
        logger.warning("mobile.session: could not checkpoint an explore turn")
    return written


def _turn_began(body: dict, now: float) -> float:
    """When this turn's packet was built, or *now* when the record has no start."""
    # The window this case's evidence is joined on is ``[started, updated]``.
    # ``started`` is when the PACKET was built, not when this record is written,
    # or the window is the pad around a single instant at the end of the turn
    # and everything the app did during it falls outside.
    began = body.get("turn_started")
    if not isinstance(began, (int, float)) or isinstance(began, bool) or began <= 0:
        return now
    return began


def _explore_result_fields(result: dict, verdict: str) -> dict:
    """The verdict, status, reason and trace of one turn's replay result."""
    return {
        "verdict": str(verdict or ""),
        "status": str(result.get("status") or ""),
        "reason": str(result.get("reason") or "")[:1200],
        "trace": list(result.get("trace") or []),
    }


def _explore_guard_stop(result: dict) -> dict:
    """The guard fingerprint a later explore-lane confirm binds to."""
    # Same fingerprint shape `case_runner._checkpoint` writes, from the
    # SAME per-submit `result` this turn's own replay returned -- so an
    # explore-lane confirm binds to the op THIS run's last turn stopped
    # on, exactly like the scripted lane's `_consume_confirm`. Not
    # carried forward from a prior turn's checkpoint, for the same
    # reason `case_runner._checkpoint` does not carry it forward either.
    return {
        "term": str(result.get("guard_term") or "")[:80],
        "op": str(result.get("guard_op") or "")[:40],
        "node": str(result.get("guard_node") or "")[: executor.MAX_GUARD_NODE_CHARS],
    }


def _explore_turn_evidence(network: object) -> dict:
    """An evidence record shaped like ``case_runner._evidence_record``'s output."""
    # Shaped like `case_runner._evidence_record`'s output so the report's
    # evidence join reads a record rather than a missing key.
    return {
        "profile": None,
        "clock_offset_ms": None,
        "pid": None,
        "skipped": "an exploratory turn takes no app-log slice",
        "slices": 0,
        "slices_written": [],
        # The app LOG is not captured for an exploratory turn; the WIRE is.
        # Two different questions, so two keys and two answers -- the
        # skipped line above is about the log alone.
        "network": case_runner.network_record(network),
    }


async def _explore_begin_network(
    run_id: str, state: object, ctx: executor.Context
) -> dict:
    """Start this turn's capture and put its record in the explore state.

    Returns the state to persist. Never raises: a capture is evidence, and
    evidence may not cost a turn.
    """
    body = dict(state if isinstance(state, dict) else {})
    try:
        tc_id = explore_turn_tc_id(body.get("turn"))
        prior = body.get("network")
        # A RE-ISSUED packet for the same turn (a refused script, a resumed
        # chat) must not leave the first capture running: the console records
        # until it is told to stop, and the second start would write the same
        # file name over a capture nothing had read.
        if isinstance(prior, dict) and prior.get("started"):
            await capture.abort_network(ctx.serial, run_id, tc_id, prior)
        body["network"] = case_runner.network_record(
            await capture.begin_network(ctx.serial, ctx.package, run_id, tc_id, 0)
        )
    except Exception:  # never-raise: evidence is not a turn
        logger.exception("mobile.session._explore_begin_network failed")
    return body


async def _explore_abort_network(
    run_id: str, state: object, ctx: executor.Context
) -> dict:
    """Stop a capture whose turn will never be replayed, and drop its record.

    Returns the state to persist. The record is REMOVED rather than kept: the
    file is gone, so a record still saying ``started`` would have the next
    begin abort something that no longer exists. Never raises.
    """
    body = dict(state if isinstance(state, dict) else {})
    prior = body.get("network")
    if not (isinstance(prior, dict) and prior.get("started")):
        return body
    try:
        await capture.abort_network(
            ctx.serial, run_id, explore_turn_tc_id(body.get("turn")), prior
        )
    except Exception:  # never-raise: evidence is not a turn
        logger.exception("mobile.session._explore_abort_network failed")
    body.pop("network", None)
    return body


async def _explore_finish_network(
    run_id: str, state: object, ctx: executor.Context
) -> dict:
    """Stop this turn's capture and return its record for the checkpoint.

    The record is READ from the state the packet build persisted, so a turn
    submitted from another chat still finds its own capture.
    """
    body = state if isinstance(state, dict) else {}
    try:
        tc_id = explore_turn_tc_id(body.get("turn"))
        return case_runner.network_record(
            await capture.finish_network(
                ctx.serial,
                ctx.package,
                run_id,
                tc_id,
                0,
                body.get("network"),
            )
        )
    except Exception as exc:  # never-raise: evidence is not a verdict
        logger.exception("mobile.session._explore_finish_network failed")
        # A BLANK record here is what made the report say the capture ran and
        # found nothing. The failure names itself instead, so the page shows a
        # reason rather than a false negative.
        return case_runner.network_record(
            {
                "stage": "finish",
                "skipped": (
                    "the capture could not be stopped and read for this turn: "
                    + str(exc)[:200]
                ),
            }
        )


def _persist_explore(run_id: str, state: object, planned: object = None) -> None:
    """Write the exploratory state back into the manifest, in place.

    *planned* is one ``TestCase``-shaped dict for the turn just submitted. It
    goes in the SAME read-modify-write as the state: this function writes the
    WHOLE manifest, so two separate writes would have the second clobber the
    first.
    """
    try:
        manifest = (run_store.read_manifest(run_id) or {}).get("content") or {}
        if not isinstance(manifest, dict):
            return
        manifest["explore"] = state if isinstance(state, dict) else {}
        if isinstance(planned, dict) and planned.get("tc_id"):
            cases = [
                body
                for body in list(manifest.get("cases") or [])
                if isinstance(body, dict)
                and str(body.get("tc_id") or "") != str(planned["tc_id"])
            ]
            cases.append(planned)
            manifest["cases"] = cases
            manifest["total"] = len(cases)
        run_store.write_manifest(run_id, manifest)
    except Exception:  # pragma: no cover - defensive
        logger.warning("mobile.session: could not persist explore state")


def _combine_queued_actions(queued: object, new_actions: object) -> dict:
    """Actions left queued by a budget stop, folded with a fresh submit.

    Delegates to :func:`tools.mobile.actions.combine_queued_actions` -- FU1
    moved the combine rule there so ``case_runner`` (which cannot import
    ``session``) can use the same rule for the SCRIPTED lane. Behaviour-
    identical; see that function's docstring for the queue-first,
    cap-refuses-by-name design.
    """
    from tools.mobile import actions as actions_mod

    return actions_mod.combine_queued_actions(queued, new_actions)


def _merge_tester_inputs(
    tester_input: str, tester_input_field: str, tester_inputs: dict | None
) -> dict:
    """The legacy single ``(field, value)`` pair plus a multi-field dict, merged
    into ONE dict :func:`submit` puts on ``Context.tester_inputs``.

    ``tester_inputs`` wins on a colliding field name -- it is the newer, more
    specific form, and a caller sending both is choosing to override the
    legacy pair rather than fighting it silently. Each field name is
    truncated to 80 chars, matching the legacy single-field cap. More than
    :data:`MOBILE_MAX_TESTER_INPUTS` fields raises ``ValueError`` naming the
    cap -- :func:`submit` returns it as the error, and runs nothing -- rather
    than dropping fields silently, so an oversized payload cannot grow the audit's ``tester_fields`` list, or the
    executor's per-field lookups, without bound.
    """
    merged: dict = {}
    legacy_field = str(tester_input_field or "").strip()[:80]
    if legacy_field and str(tester_input or ""):
        merged[legacy_field] = str(tester_input)
    if isinstance(tester_inputs, dict):
        for raw_name, value in tester_inputs.items():
            name = str(raw_name or "").strip()[:80]
            if name and str(value or ""):
                merged[name] = str(value)
    if len(merged) > MOBILE_MAX_TESTER_INPUTS:
        raise ValueError(
            "this submit names %d tester fields; one submit takes at most %d "
            "(MOBILE_MAX_TESTER_INPUTS). Nothing was run: answer only the "
            "fields this screen asks for and send the rest on a later submit."
            % (len(merged), MOBILE_MAX_TESTER_INPUTS)
        )
    return merged


SINGLE_ACTION_NOTICE = (
    "This script carried one action. A script may carry up to "
    "{max_actions} -- a tap, the type it triggers and the assert that "
    "checks it usually fit in one script together, and each extra round trip "
    "through this tool is one the tester's own client timer is also paying "
    "for. Batch the next few actions you already know you want when they do "
    "not depend on an unpredictable reply."
)


def _single_action_notice(raw_script: object) -> str:
    """ "" unless *raw_script* parses to exactly one action.

    Never raises and never refuses: read AFTER `case_runner.submit_case`
    already validated and replayed the same script, so a parse failure here
    can only mean the script changed between the two reads, which never
    happens on this call's own object. Audit item 3 -- the schema already
    allowed up to `actions.MAX_MODEL_ACTIONS`; nothing said so where a planner
    would see it before scripting one action per submit. VERIFIED (not
    UNVERIFIED): `actions_mod.parse_script`'s own docstring says it
    "[a]ccepts the packet shape ({\"actions\": [...]}), a bare list, or a
    JSON string of either" -- `tools/mobile/actions.py:636-642` -- so this
    call on the raw string `submit()` already received is exactly one of
    the three documented shapes, not a new assumption.
    """
    try:
        parsed = actions_mod.parse_script(raw_script)
        script = parsed.get("content")
        if script is not None and len(script.actions) == 1:
            return SINGLE_ACTION_NOTICE.format(
                max_actions=actions_mod.MAX_MODEL_ACTIONS
            )
    except Exception:
        logger.debug("mobile.session._single_action_notice failed", exc_info=True)
    return ""


async def submit(
    run_id: str,
    tc_id: str,
    raw_script: object,
    *,
    session_token: str = "",
    tester_input: str = "",
    tester_input_field: str = "",
    tester_inputs: dict | None = None,
    confirm_destructive: bool = False,
    budget: object = None,
    route_expect: object = None,
) -> dict:
    """Replay one answered packet and return the verdict plus the NEXT packet.

    The tester's value(s) are put on ``Context.tester_inputs`` and nowhere
    else: not returned, not checkpointed, not logged and not part of any dict
    this function hands back. ``tester_input``/``tester_input_field`` is the
    legacy single-field pair; ``tester_inputs`` is a dict for a submit that
    answers TWO OR MORE fields at once (a password plus a one-time code, say)
    -- see :func:`_merge_tester_inputs`. The FIELD NAMES travel; the values do
    not.
    """
    try:
        resolved = resolve(run_id, session_token)
        if resolved.get("error"):
            return resolved
        lock_reaper.touch_activity(run_id)
        body = resolved["content"] or {}
        ctx = context_for(body)
        _cap_submit_budget(ctx, budget)
        field = str(tester_input_field or "").strip()[:80]
        try:
            merged_inputs = _merge_tester_inputs(
                tester_input, tester_input_field, tester_inputs
            )
        except ValueError as exc:
            return {"error": str(exc), "content": None}
        _hold_tester_inputs(run_id, ctx, merged_inputs)
        ctx.confirm_destructive = bool(confirm_destructive)
        # S10 saved route (PROVISIONAL): empty unless the wrapper is replaying a route.
        ctx.route_expect = (
            tuple(str(item) for item in route_expect)
            if isinstance(route_expect, (list, tuple))
            else ()
        )
        if str(body.get("lane")) == LANE_EXPLORE:
            return await _submit_explore_held(run_id, raw_script, ctx, body)
        return await _submit_suite(run_id, tc_id, raw_script, ctx, body, field)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.submit failed")
        return {"error": str(exc), "content": None}


def _cap_submit_budget(ctx: executor.Context, budget: object) -> None:
    """Cap this submit's replay budget so ``PACKET_RESERVE_S`` is left over."""
    if budget is None:
        return
    # Caps THIS submit's replay so PACKET_RESERVE_S is still there for
    # next_packet afterward -- without this, a slow action can spend
    # the whole call budget on the replay and next_packet's busy
    # reply is left with nothing to build a packet from. Never above
    # SUBMIT_BUDGET_S: that constant is unchanged and still the
    # ceiling.
    ctx.budget_s = max(
        1.0,
        min(executor.SUBMIT_BUDGET_S, budget.remaining() - PACKET_RESERVE_S),
    )


def _hold_tester_inputs(run_id: str, ctx: executor.Context, merged: object) -> None:
    """Remember this call's inputs for the run and put every held one on *ctx*."""
    # HELD FOR THE RUN (fix round 3, item 1). On the Air run the tester
    # gave the login fields once and was asked again on five of eight
    # turns, because a value lived on the Context for ONE call. It is now
    # held in process memory by `held_inputs` -- never on disk, and never
    # in this module, which keeps no state in memory -- and every later
    # submit of the same run carries it, so `ask_tester` finds it already
    # supplied. A value given on THIS call wins over a held one. The hold
    # is dropped when the SUBMIT ITSELF reaches the report (both lanes,
    # below); any other way a run ends relies on the sliding TTL.
    from tools.mobile import held_inputs

    held_inputs.remember(run_id, merged)
    carried = held_inputs.recall(run_id)
    if carried or merged:
        ctx.tester_inputs = dict(carried, **dict(merged or {}))


async def _submit_explore_held(
    run_id: str, raw_script: object, ctx: executor.Context, body: dict
) -> dict:
    """Submit on the explore lane; drop the held inputs once it reports."""
    from tools.mobile import held_inputs

    explored = await _submit_explore(run_id, raw_script, ctx, body)
    content = explored.get("content") if isinstance(explored, dict) else None
    if isinstance(content, dict) and content.get("state") == STATE_REPORT:
        held_inputs.forget(run_id)
    return explored


async def _submit_suite(
    run_id: str,
    tc_id: str,
    raw_script: object,
    ctx: executor.Context,
    body: dict,
    field: str,
) -> dict:
    """Replay one suite-lane answer and build the verdict plus next-step reply."""
    from tools.mobile import held_inputs

    # Scoped to THIS tc_id, not a run-wide scan -- see `_prior_guard_stop`.
    ctx.prior_guard_stop = _prior_guard_stop(run_id, tc_id)
    loaded = load_case(run_id, tc_id)
    if loaded.get("error"):
        return loaded
    result = await case_runner.submit_case(run_id, loaded["content"], ctx, raw_script)
    if result.get("error"):
        return result
    content = dict(result.get("content") or {})
    follow = (scheduler.next_case(run_id) or {}).get("content") or {}
    state = (
        STATE_RUNNING
        if content.get("packet")
        else (
            STATE_REPORT
            if follow.get("finished")
            else STATE_GATE
            if follow.get("gate")
            else STATE_RUNNING
        )
    )
    if state == STATE_REPORT:
        await _finish_evidence(run_id, body)
        held_inputs.forget(run_id)
    return {
        "error": None,
        "content": {
            "state": state,
            "case": content,
            "packet": content.get("packet"),
            "next": follow,
            "field": field,
            "resolved": body,
            "notice": _single_action_notice(raw_script),
        },
    }


def _explore_needs_model(reason: str, resolved: dict) -> dict:
    """A refused explore turn: logged, then handed back to the model to rewrite."""
    case_runner.note_refusal(reason)
    return {
        "error": None,
        "content": {
            "state": STATE_RUNNING,
            "case": {"status": case_runner.NEEDS_MODEL, "reason": reason},
            "packet": None,
            "next": {},
            "field": "",
            "resolved": resolved,
        },
    }


def _prime_explore_turn(run_id: str, ctx: executor.Context, payload: dict) -> None:
    """Expect the apps this turn names; carry the screen only after an inert turn."""
    from tools.mobile import actions as actions_mod

    ctx.expect_apps = tuple(actions_mod.expect_apps_from(payload))
    ctx.carry_screen = carry_policy.allowed(run_id)


def _noted_outcome(run_id: str, replayed: dict) -> dict:
    """The replay's content, after noting whether the turn actuated anything."""
    outcome = replayed.get("content") or {}
    carry_policy.note(run_id, outcome.get("trace"))
    return outcome


async def _submit_explore(
    run_id: str, raw: object, ctx: executor.Context, resolved: dict
) -> dict:
    """One exploratory turn: replay the actions, then fold the reply."""
    from tools.mobile import actions as actions_mod

    # DECODED, not type-tested. `qa_submit_mobile_step` declares `script: str`,
    # so this used to be `{}` on every real client path: the whole JSON string
    # went to `parse_script`, `Script`'s `extra="forbid"` refused the advertised
    # `finding` key, and nothing replayed -- and when a model retried with a
    # bare actions array, `apply_turn_result` below got `{}` and never read the
    # finding. Two failures, one cause, and no test saw either because every
    # test passed a dict into this helper.
    payload = actions_mod.decode_reply(raw)
    # SERVER-SIDE queue (defect F2): actions a prior budget stop left unreached
    # ride the persisted explore state and run FIRST, ahead of whatever the
    # model just submitted -- see _combine_queued_actions.
    queued_in = list((resolved.get("explore") or {}).get("queued_actions") or [])
    if queued_in:
        combined = _combine_queued_actions(
            queued_in, list(payload.get("actions") or [])
        )
        if not combined.get("ok"):
            return _explore_needs_model(str(combined.get("reason") or ""), resolved)
        payload["actions"] = combined["actions"]
    # A folded queue was already held to both caps by the combine, so only a
    # bare submission is held to the model cap here.
    parsed = actions_mod.parse_script(
        payload.get("actions"),
        max_actions=(
            actions_mod.MAX_ACTIONS if queued_in else actions_mod.MAX_MODEL_ACTIONS
        ),
    )
    if parsed.get("error"):
        return _explore_needs_model(parsed["error"], resolved)
    # HAND-OVER TO THE TARGET APP (fix round 3, item 1). A multi-app goal
    # (App Tester -> the app under test) moves into a second app, and every guard that
    # asks "is this the app under test" reads `ctx.package` -- which stayed
    # the run's FIRST app for the whole Air run, so five turns on the right
    # screen were refused as SCREEN_NOT_THE_APP. `explore_runner.handover`
    # accepts the model's `app` only when it is the package the SERVER saw in
    # front when it built this turn's packet, and never a system surface.
    # Applied BEFORE the replay and carried on the explore state that
    # `apply_turn_result` persists, so `context_for` reads it on every later
    # call. `clear_app_data` keeps `ctx.home_package`.
    handed = explore_runner.handover(resolved.get("explore") or {}, payload)
    if handed:
        resolved = dict(resolved)
        resolved["explore"] = dict(resolved.get("explore") or {}, app=handed)
        ctx.package = handed
        ctx.activity = ""
    # Run-wide evidence, read from the checkpoints this run already wrote and
    # passed in EXPLICITLY. See ``_explore_prior_verified``.
    ctx.prior_turn_blocked = _explore_prior_turn_blocked(run_id)
    ctx.prior_verified = _explore_prior_verified(
        run_id
    )  # WHAT HAPPENS AFTER A GUARD HIT, from this run's own charter. The guard
    # itself is unchanged and no value here lets an action through it:
    # ``charter.guard_policy`` maps ``destructive: none`` to REFUSE, and the
    # executor then ENDS the attempt by name instead of pausing for the
    # tester. An explicit input, set beside ``prior_verified`` for the same
    # reason -- the executor knows nothing about runs or charters.
    ctx.guard_refuses = (
        charter_mod.guard_policy(
            ((resolved.get("explore") or {}).get("charter") or {}).get("destructive")
        )
        == charter_mod.REFUSE
    )
    # THIS turn's own id, computed BEFORE the replay below. The exact-match
    # lookup alone no longer suffices here: `explore_runner.next_packet`
    # bumps `state["turn"]` on EVERY call, so a confirm submitted after the
    # scheduler already advanced past this turn names a `tc_id` no case was
    # ever checkpointed under. See `_prior_guard_stop_explore`.
    ctx.prior_guard_stop = _prior_guard_stop_explore(
        run_id, explore_turn_case_id(resolved.get("explore"))
    )
    # THE RECORDER, on the lane that shipped without one. `case_runner` has
    # started a clip around ITS replay since v1.87.0; this is the other replay
    # site and it had none, so every exploratory run -- which is the lane the
    # testers actually use -- produced a report with no video while the feature
    # read as shipped. Measured on run mrun-20260910-180207-2ee014: five cases,
    # zero media records, nothing failed. One question, two sites, one wired.
    #
    # The id comes from `explore_turn_case_id`, the same producer the checkpoint
    # below writes the case under.
    clip_tc_id = explore_turn_case_id(resolved.get("explore"))
    clip = (
        await media.start_clip(run_id, clip_tc_id, parsed["content"], ctx.serial)
    ).get("content")
    _prime_explore_turn(run_id, ctx, payload)
    replayed = await executor.replay(parsed["content"], ctx)
    if replayed.get("error"):
        # The turn is over and no checkpoint will be written for it, so the
        # capture this turn started has no later reader. Stop it here or the
        # console records the tester's device until something displaces it.
        # The RECORDER is the same shape of leak, one artifact over: no
        # checkpoint means no case to file a record onto, so the clip is
        # stopped and deleted rather than stored -- `case_runner` calls
        # `abandon` on its own error path for exactly this reason.
        await media.abandon(ctx.serial, clip)
        _persist_explore(
            run_id,
            await _explore_abort_network(run_id, resolved.get("explore") or {}, ctx),
        )
        return replayed
    outcome = _noted_outcome(run_id, replayed)
    folded = explore_runner.apply_turn_result(resolved.get("explore") or {}, payload)
    if folded.get("error"):
        return folded
    turn = folded.get("content") or {}
    # Carried on `turn["state"]`, not `turn` itself: `committed_state` below is
    # built from `turn.get("state")`, and that is the ONLY part of `turn`
    # `_persist_explore` writes back into `resolved["explore"]` -- a queue
    # attached anywhere else on `turn` would never reach the next submit.
    if isinstance(turn.get("state"), dict):
        turn["state"]["queued_actions"] = outcome.get("queued_actions") or []
    # The turn just replayed, checkpointed as a case. ``resolved["explore"]``
    # carries the turn NUMBER this submit answered (``next_turn`` incremented
    # and persisted it before the packet went out), so the id is the turn's own
    # and is the same on a resubmit.
    before = resolved.get("explore") or {}
    finding = _latest_finding(turn.get("state"))
    # ONE producer for this turn's verdict, computed once from the replay and
    # read by BOTH consumers below -- the DISK record and the CHAT reply. They
    # are two artifacts with two lifetimes, so they stay two sites; what was
    # wrong is that each was its own producer, writing "" unconditionally.
    #
    # Only a turn whose ``done()`` judged the goal writes a verdict; every
    # other turn keeps "" plus its status, because an exploratory turn has no
    # expected result to be judged against. It lands in the ordinary
    # ``verdict`` field, exactly as the suite lane writes it, so
    # ``run_store.DONE_VERDICTS`` and every reader of it work unchanged and no
    # verdict reaches the report by a second route.
    #
    # The condition is STATED, not inferred from the producer. The premise this
    # line used to rest on -- "the executor returns a verdict only from a
    # ``done`` op" -- is false: ``executor.replay``'s no-done exit returns
    # ``unverified`` too, so reading the field alone recorded a turn that
    # merely acted as one that had reached a verdict. ``unverified`` IS in
    # ``DONE_VERDICTS``, so that would have made D1's coverage count true and
    # meaningless in the same breath -- every turn "with a verdict", none of
    # them judged.
    ended = any(
        str((entry.get("action") or {}).get("op") or "") == "done"
        for entry in list(outcome.get("trace") or [])
        if isinstance(entry, dict)
    )
    verdict = str(outcome.get("verdict") or "") if ended else ""
    # The turn HAS been replayed, so its capture is complete. Stopped before the
    # checkpoint, because the checkpoint is what carries the record to the report.
    net = await _explore_finish_network(run_id, before, ctx)
    checkpoint = _checkpoint_explore_turn(
        run_id, before, outcome, finding, verdict=verdict, network=net
    )
    # AFTER the checkpoint, and that ordering is the contract, not a style
    # choice. `media.remember` APPENDS onto the case record; in this lane the
    # checkpoint above is what CREATES that record, so calling this first would
    # find no case, log the loss and drop the clip on the floor. The case lane
    # is the other way round only because `start_case` wrote its record before
    # the submit began. Nothing rewrites the case after this point, so the
    # records survive without a carry-forward of their own.
    await media.finish_step(
        run_id,
        clip_tc_id,
        ctx.serial,
        clip,
        str((outcome.get("screen") or {}).get("screen_id") or ""),
    )
    # The replay HAPPENED, so the number this turn was handed is now earned.
    # This is the only writer that ADVANCES ``committed_turn`` to a number the
    # ladder has not already earned, and it
    # sits below every path that ends without a replay: the parse refusal above
    # returns before ``executor.replay``, a replay error returns before this
    # line, and a packet nobody ever answers never reaches this module at all.
    # Each of those leaves the ledger where it was, so
    # ``explore_runner.next_turn`` re-allocates the SAME number next time and the
    # report grows neither a gap nor a duplicate card.
    #
    # No coercion guard here, and no ``# pragma``. ``apply_turn_result`` above
    # calls ``explore_runner.stop_reason``, which coerces this same ``turn``, so
    # on every path where ``state["stop"]`` is empty a junk turn number has
    # already been answered with a refusal before this line -- a guard here would
    # be a branch no test can enter, an ungraded clause wearing a note.
    #
    # Where ``stop`` is NOT empty, ``stop_reason`` returns before its coercion
    # and this line does raise; ``submit``'s own try/except contains it and the
    # caller gets an error dict, never an exception. That corner is why the guard
    # is deleted rather than corrected: it used to answer with
    # ``committed_turn = 0``, which restarts numbering at TC-001 over cards the
    # run already wrote.
    committed_state = dict(turn.get("state") or {})
    committed_state["committed_turn"] = int(committed_state.get("turn") or 0)
    # This turn's capture is finished and its record now lives on the case
    # record. Leaving it on the state would have the NEXT turn's begin treat it
    # as a capture still running and abort a file that is already gone.
    committed_state.pop(
        "network", None
    )  # THE REFUSE, RECORDED AS A STOP. The executor marks a charter-driven
    # refusal explicitly (``guard_refused``), and a refused run is OVER -- so
    # the state says so and every reader under ``stop_reason`` sees it.
    # Without this the reply would carry the refusal text and still report a
    # running run: a success-shaped message behind a refusal, which is exactly
    # what this lane's rule forbids.
    refused = bool(outcome.get("guard_refused"))
    if refused:
        committed_state["stop"] = explore_runner.STOP_GUARD_REFUSED
    _persist_explore(
        run_id,
        committed_state,
        planned=_explore_planned_case(checkpoint["tc_id"], before, finding),
    )
    # ONE derivation of "has this run stopped", read by BOTH consumers below.
    # The two used to be two copies of the same condition, and a refusal must
    # move both or the chat reply invites a next turn the run will not give.
    stopped = refused or str(turn.get("status") or "") != explore_runner.RUNNING
    if stopped:
        await _finish_evidence(run_id, resolved)
    return {
        "error": None,
        "content": {
            "state": STATE_REPORT if stopped else STATE_RUNNING,
            "case": {
                "tc_id": checkpoint["tc_id"],
                "title": checkpoint["title"],
                "status": str(outcome.get("status") or ""),
                "verdict": verdict,
                "reason": str(outcome.get("reason") or ""),
                "trace": list(outcome.get("trace") or []),
            },
            "packet": None,
            "next": {},
            "notice": (
                explore_runner.GUARD_REFUSED_NOTICE
                if refused
                else str(turn.get("notice") or "")
            ),
            "field": "",
            "resolved": resolved,
        },
    }


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def audit_detail(
    *,
    run_id: str = "",
    tc_id: str = "",
    verdict: str = "",
    status: str = "",
    field: str = "",
    fields: list[str] | None = None,
    step: str = "",
    apply: object = None,
) -> dict:
    """The ``detail`` dict an audit row may carry. **No value can reach it.**

    ``audit_log.record_event`` writes ``detail`` VERBATIM -- there is no
    redaction hook anywhere below this point -- and ``run_store.redact`` is
    mark-based plus a fixed list of credential-NAMED keys. A tester names their
    own field, so ``{"inputs": {"tenantToken": "..."}}`` would be written in
    clear. This function therefore has no parameter that could carry a value: it
    takes the field NAME and emits a MARKED entry, and the marker is what makes
    the ``redact`` pass below effective rather than incidental.
    """
    try:
        detail: dict = {
            "run_id": str(run_id or "")[:80],
            "tc_id": str(tc_id or "")[:16],
            "verdict": str(verdict or "")[:24],
            "status": str(status or "")[:32],
        }
        if step:
            detail["step"] = str(step)[:48]
        if apply is not None:
            detail["apply"] = bool(apply)
        if field:
            detail["tester_field"] = {
                "secret": True,
                "field": str(field)[:80],
                "value": run_store.SECRET_MASK,
            }
        if fields:
            names = [str(name)[:80] for name in fields if str(name or "").strip()][
                :MOBILE_MAX_TESTER_INPUTS
            ]
            if names:
                detail["tester_fields"] = [
                    {"secret": True, "field": name, "value": run_store.SECRET_MASK}
                    for name in names
                ]
        return run_store.redact(detail)
    except Exception:  # pragma: no cover - defensive
        logger.warning("mobile.session.audit_detail failed", exc_info=True)
        return {}


def screen_of(dump: object, activity: str = "", display: object = None) -> dict:
    """Prune a dump. Here so a handler never touches raw XML itself.

    ``display`` is the device's natural size where the caller has it. Without it
    `prune` derives the frame from the dump's own windows, which over-estimates
    rather than under-estimates and so cannot lose the screen -- see
    `perception._display_frame`.

    NO PRODUCTION CALLER TODAY, and one thing to know before writing the first
    one: ``activity`` defaults to ``""``, and a screen pruned without an activity
    gets a screen_id hashed on two dimensions instead of three -- a DIFFERENT id
    from the one `executor` stamps on every trace entry. If a screen from here is
    ever stored in a run, pass ``await executor.resolve_activity(ctx)``, or the
    report's join silently loses every picture (measured; see
    ``tests/mobile/test_mobile_screen_id_producer``). This function takes no run
    id, so that module's class pin cannot see it.
    """
    return composite.observe(dump, activity, display=display)
