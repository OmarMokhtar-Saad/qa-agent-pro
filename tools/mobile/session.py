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

**This module does not read the kill-switch.** There are exactly two readers in
``tools/mobile`` -- ``provisioner.run(apply=True)`` and
``provisioner.start_detached``, i.e. the two places that spend bytes -- plus
``preflight.flag_state`` for reporting, and the MCP boundary's own
``mcp_handlers._mobile_lane_enabled()``. A third reader here would be a fourth
copy of one rule, and the copy is the defect: the guard belongs at the process
that acts and at the boundary that is called, and this module is neither.
"""

from __future__ import annotations

import json
import logging
import secrets
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from config.settings import settings
from tools.device_manager import _valid_device_id, valid_package_name
from tools.mobile import (
    adb,
    case_runner,
    downloader,
    emulator,
    executor,
    explore_runner,
    importers,
    locks,
    paths,
    perception,
    platform_info,
    preflight,
    provisioner,
    run_store,
    scheduler,
)
from tools.mobile_evidence import capture

logger = logging.getLogger(__name__)

STATE_NEEDS_FLAG = "needs_flag"
STATE_PROVISIONING = "provisioning"
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

#: The longest this module will ever wait on the device inside one tool call.
#: Well under a client's ~50s tool timeout, and passed EXPLICITLY to
#: ``wait_boot`` -- whose own default is 240s, so an omitted keyword is the
#: defect this constant exists to prevent.
DEVICE_WAIT_BUDGET_S = 20

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


def _busy(resolved: object, tc_id: str) -> dict:
    """The bounded-call reply: a pointer, not a packet, and nothing half-done."""
    return {
        "error": None,
        "content": {
            "state": STATE_BUSY,
            "packet": None,
            "tc_id": str(tc_id or ""),
            "resolved": resolved if isinstance(resolved, dict) else {},
        },
    }


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
        # EXISTENCE FIRST. acquire_lease mkdir -p's the run directory, so
        # claiming before checking let any well-formed id mint a run in the
        # shared state root -- and the reply then said the run did not exist
        # while its lease sat on disk. The check lives here rather than in the
        # handlers for the reason the docstring above already gives: two copies
        # of one test is how the two handlers drift.
        if not (run_store.read_manifest(run_id).get("content") or {}):
            return {
                "error": (
                    "No run `" + str(run_id)[:64] + "` on this machine. "
                    "Nothing was created. `qa_mobile_status` lists the runs "
                    "this install knows about."
                ),
                "content": None,
            }
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
            return {
                "error": None,
                "content": {
                    "session_token": token,
                    "state": STATE_TAKEN_OVER,
                    "holder": str(body.get("holder") or ""),
                    "taken_over_from": "",
                },
            }
        status = run_store.touch_lease(run_id, token)
        state = str((status.get("content") or {}).get("state") or run_store.HELD)
        if state == run_store.TAKEN_OVER:
            return {
                "error": None,
                "content": {
                    "session_token": token,
                    "state": STATE_TAKEN_OVER,
                    "holder": str((status.get("content") or {}).get("holder") or ""),
                    "taken_over_from": "",
                },
            }
        return {
            "error": None,
            "content": {
                "session_token": token,
                "state": run_store.HELD,
                "holder": token,
                "taken_over_from": str(body.get("taken_over_from") or ""),
            },
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.claim failed")
        return {"error": str(exc), "content": None}


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------


async def ensure_device(
    *, avd: str = "", serial: str = "", budget_s: int = DEVICE_WAIT_BUDGET_S
) -> dict:
    """Attach to a booted emulator, or start one and hand back a pointer.

    ``{"error", "content": {"state", "serial", "detail"}}`` with ``state`` one of
    ``ready`` / :data:`STATE_BOOTING`.

    ``emulator.boot`` is deliberately NOT called: it polls to
    ``QA_MOBILE_BOOT_TIMEOUT_S`` (240s by default), which is four times a
    client's tool timeout, so a tester would see a dead editor rather than a
    message. The bounded pair is ``emulator.start`` (spawn, detached, returns at
    once) plus ``emulator.wait_boot`` with an EXPLICIT budget.
    """
    try:
        name = str(avd or provisioner.AVD_NAME)
        budget = max(1, int(budget_s or DEVICE_WAIT_BUDGET_S))
        if serial:
            # A tester-chosen (or already-adopted) device: never probe for
            # the hardcoded AVD and never spawn a second emulator under it.
            waited = await emulator.wait_boot(serial, timeout=budget)
            if waited.get("error"):
                return {
                    "error": None,
                    "content": {
                        "state": STATE_BOOTING,
                        "serial": serial,
                        "detail": str(waited["error"])[:400],
                    },
                }
            return {
                "error": None,
                "content": {"state": "ready", "serial": serial, "detail": ""},
            }
        running = await emulator.find_running(name)
        if running.get("error"):
            return running
        serial = str((running.get("content") or {}).get("serial") or "")
        if serial:
            waited = await emulator.wait_boot(serial, timeout=budget)
            if waited.get("error"):
                return {
                    "error": None,
                    "content": {
                        "state": STATE_BOOTING,
                        "serial": serial,
                        "detail": str(waited["error"])[:400],
                    },
                }
            return {
                "error": None,
                "content": {"state": "ready", "serial": serial, "detail": ""},
            }
        started = await emulator.start(name)
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
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.ensure_device failed")
        return {"error": str(exc), "content": None}


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
    ``state`` either ``ready`` or :data:`STATE_BOOTING`.

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
        avd = str(manifest.get("avd") or provisioner.AVD_NAME)
        alive = (await device_alive(serial)).get("content") or {}
        if alive.get("alive"):
            return {
                "error": None,
                "content": {
                    "state": "ready",
                    "serial": serial,
                    "detail": "",
                    "rebooted": False,
                },
            }
        span = int(budget.remaining()) if budget is not None else DEVICE_WAIT_BUDGET_S
        ready = await ensure_device(
            avd=avd, budget_s=max(1, min(DEVICE_WAIT_BUDGET_S, span))
        )
        if ready.get("error"):
            return ready
        state = ready.get("content") or {}
        if str(state.get("state") or "") != "ready":
            return {
                "error": None,
                "content": {
                    "state": STATE_BOOTING,
                    "serial": str(state.get("serial") or ""),
                    "detail": str(state.get("detail") or ""),
                    "rebooted": False,
                },
            }
        fresh = str(state.get("serial") or "")
        if fresh and fresh != serial:
            manifest["serial"] = fresh
            run_store.write_manifest(run_id, manifest)
        return {
            "error": None,
            "content": {
                "state": "ready",
                "serial": fresh or serial,
                "detail": "",
                "rebooted": True,
            },
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.ensure_run_device failed")
        return {"error": str(exc), "content": None}


def _install_state_path() -> Path:
    return paths.state_file(INSTALL_FILE)


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

    Reads the kill-switch ITSELF. The handler that normally calls this checks
    both the lane predicate and ``apply``, but those are guards on a CALLER, and
    this function spawns a detached install on the tester's device -- an effect
    that outlives the call. A guard on a caller is only as good as the list of
    callers, which is the same lesson ``provisioner.run`` learned when a check on
    the parent process did not cover ``python -m``.
    """
    try:
        if not settings.qa_mobile_run_enabled:
            return {
                "error": (
                    "Refusing to install: the mobile lane needs "
                    "`QA_MOBILE_RUN_ENABLED=true` in `.env`. Nothing was "
                    "installed and no process was started."
                ),
                "content": None,
            }
        if not valid_package_name(package):
            return {
                "error": (
                    "Refusing " + repr(str(package)[:60]) + " as a package name."
                ),
                "content": None,
            }
        if not _valid_device_id(serial):
            return {
                "error": "Refusing " + repr(str(serial)[:40]) + " as a device id.",
                "content": None,
            }
        path = Path(str(apk_path or "")).expanduser()
        if path.suffix.lower() != ".apk" or not path.is_file():
            return {
                "error": (
                    "No .apk file at "
                    + str(path)[:200]
                    + ". Give the full path to the file, and nothing is installed."
                ),
                "content": None,
            }
        paths.ensure_tree()
        command = [
            adb.resolve_adb(),
            "-s",
            str(serial),
            "install",
            "-r",
            "-g",
            str(path),
        ]
        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        kwargs.update(platform_info.detach_kwargs())
        proc = subprocess.Popen(command, **kwargs)  # noqa: S603 - argv, no shell
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


async def install_state(serial: str, package: str) -> dict:
    """``{installed, pending, apk, started}`` -- answered by the DEVICE."""
    try:
        record = _read_install_record()
        installed = False
        listed = await adb.installed_packages(serial)
        if not listed.get("error"):
            installed = str(package) in list(listed.get("content") or [])
        pending = bool(record.get("package") == str(package) and not installed)
        return {
            "error": None,
            "content": {
                "installed": installed,
                "pending": pending,
                "apk": str(record.get("apk") or ""),
                "started": float(record.get("started") or 0),
            },
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.install_state failed")
        return {"error": str(exc), "content": None}


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
                continue
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
                continue
            return {
                "run_id": run_id,
                "state": str(resolved.get("state") or ""),
                "done": int(resolved.get("done") or 0),
                "total": int(resolved.get("total") or 0),
            }
        return None
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile.session.find_resumable_run failed")
        return None


def plan_suite_run(
    cases: object,
    *,
    package: str,
    serial: str,
    source: str = "",
    filters: object = None,
    avd: str = "",
) -> dict:
    """Order, persist and create a suite run. ``{"error", "content": {...}}``."""
    try:
        ordered = scheduler.order_cases(cases, filters)
        if ordered.get("error"):
            return ordered
        kept = list((ordered.get("content") or {}).get("cases") or [])
        if not kept:
            return {
                "error": (
                    "No case survived the filters, so no run was created "
                    "(applied: "
                    + (
                        ", ".join((ordered.get("content") or {}).get("applied") or [])
                        or "none"
                    )
                    + ")."
                ),
                "content": None,
            }
        run_id = mint_run_id()
        planned = scheduler.plan_run(
            run_id,
            kept,
            None,
            manifest_extra={
                "lane": LANE_SUITE,
                "package": str(package or ""),
                "serial": str(serial or ""),
                "avd": str(avd or provisioner.AVD_NAME),
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
    watch_for: object = (),
    avd: str = "",
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
        state = explore_runner.new_state(text, watch_for)
        created = run_store.create_run(
            run_id,
            {
                "lane": LANE_EXPLORE,
                "package": str(package or ""),
                "serial": str(serial or ""),
                "avd": str(avd or provisioner.AVD_NAME),
                "order": [],
                "total": 0,
                "cases": [],
                "explore": state,
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
    return executor.Context(
        serial=str(body.get("serial") or ""),
        package=str(body.get("package") or ""),
        activity=str(body.get("activity") or ""),
    )


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
        explore = manifest.get("explore")
        explore = explore if isinstance(explore, dict) else {}
        point: dict = {}
        state = STATE_RUNNING
        if str(manifest.get("lane") or "") == LANE_EXPLORE:
            stop = explore_runner.stop_reason(explore)
            state = STATE_REPORT if stop else STATE_RUNNING
        else:
            point = (scheduler.next_case(run_id) or {}).get("content") or {}
            if point.get("finished"):
                state = STATE_REPORT
            elif point.get("gate"):
                state = STATE_GATE
        # D7 (2026-09-03): an unfinished run whose heartbeat stopped. Two runs
        # from the audit -- mrun-20260903-103316-b09ee4 (TC-003 stuck in
        # "planning", TC-004 never started) and ...103359-5b01fc -- still held
        # a lease, had no report, and `qa_mobile_status` reported them as
        # plain "running", so a tester had no way to tell a run that was
        # thinking from one nothing was driving. The lease's own heartbeat age
        # is the signal; STALE is the same threshold a takeover uses, so the
        # state and the takeover rule cannot drift apart.
        lease_age = float(lease.get("age") or 0.0)
        if (
            state not in (STATE_REPORT, STATE_GATE)
            and str(lease.get("state") or run_store.NONE) != run_store.NONE
            and lease_age > run_store.LEASE_STALE_S
        ):
            state = STATE_ABANDONED
        return {
            "error": None,
            "content": {
                "run_id": str(run_id),
                "state": state,
                "lane": str(manifest.get("lane") or LANE_SUITE),
                "package": str(manifest.get("package") or ""),
                "serial": str(manifest.get("serial") or ""),
                "avd": str(manifest.get("avd") or provisioner.AVD_NAME),
                "source": str(manifest.get("source") or ""),
                "total": int(manifest.get("total") or 0),
                "done": int(point.get("done") or 0),
                "failed": list(point.get("failed") or []),
                "next_tc_id": str(point.get("tc_id") or ""),
                "gate": bool(point.get("gate")),
                "finished": bool(point.get("finished")),
                "explore": explore,
                "holder": str(lease.get("holder") or ""),
                "lease_state": str(lease.get("state") or run_store.NONE),
                "lease_age": lease_age,
            },
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.resolve failed")
        return {"error": str(exc), "content": None}


def summary(run_id: str) -> dict:
    """Every checkpoint body for a run, newest verdicts included."""
    return run_store.list_cases(run_id)


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


def take_device_lock(owner: str, *, lease: str = "") -> dict:
    """One run drives the emulator at a time. A refusal is CONTENT, not an error.

    *owner* is a ``run_id`` once there is one, and a label from
    :func:`new_provisioning_owner`
    before that. Acquisition is idempotent for the same owner in the same
    process -- it performs no syscall at all -- which is what lets every
    device-touching entry take the lock without counting who took it first.
    """
    return locks.acquire(locks.EMULATOR_LOCK, owner=str(owner), lease=str(lease or ""))


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
    return locks.release(
        locks.EMULATOR_LOCK,
        owner=str(owner),
        lease=str(lease or ""),
        as_holder=bool(as_holder) and not lease,
        force=bool(force),
    )


def relabel_device_lock(from_owner: str, to_owner: str) -> dict:
    """Hand the device from the provisioning phase to the run that now owns it.

    The SAME file descriptor keeps the SAME kernel lock. Deliberately not
    release-then-reacquire, which would open a window in which another process
    could take the device between the two.
    """
    return locks.relabel(
        locks.EMULATOR_LOCK, from_owner=str(from_owner), to_owner=str(to_owner)
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
            state = body.get("explore") or {}
            if budget is not None and budget.expired():
                # Checked BEFORE the turn, never during: a turn dumps the screen
                # and writes state, and abandoning one half-done is worse than
                # answering with a pointer.
                return _busy(body, "")
            turn = await explore_runner.next_turn(run_id, state, ctx)
            if turn.get("error"):
                return turn
            content = turn.get("content") or {}
            _persist_explore(run_id, content.get("state"))
            running = str(content.get("status") or "") == explore_runner.RUNNING
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
        if budget is not None and budget.expired():
            # start_case force-stops the app, relaunches it and dumps the screen.
            # With the budget gone that work would land after the client has
            # already given up on the call, so nothing is started at all.
            return _busy(body, str(body.get("next_tc_id") or ""))
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
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.next_packet failed")
        return {"error": str(exc), "content": None}


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
    run_id: str, state: object, outcome: object, finding: str = ""
) -> dict:
    """Write ONE exploratory turn as a case-shaped record. Never raises.

    Best-effort exactly like ``run_store.write_screen``: a lost checkpoint may
    never lose a turn's replay, so nothing here can return an error into the
    submit path. Returns the body it wrote (or tried to), because the caller
    reports the same id and title back to the tester.

    The record carries a STATUS and no verdict, deliberately. An exploratory
    turn has no expected result, so it has no verdict to earn;
    ``report._verdict_of`` then shows the status and explicitly never invents a
    pass, which is the honest rendering of a turn that simply happened.
    """
    body = state if isinstance(state, dict) else {}
    result = outcome if isinstance(outcome, dict) else {}
    try:
        turn = max(1, int(body.get("turn") or 1))
    except (TypeError, ValueError, OverflowError):
        turn = 1
    tc_id = explore_turn_tc_id(turn)
    goal = " ".join(str(body.get("goal") or "").split())[:200] or "no goal recorded"
    now = time.time()
    written = {
        "tc_id": tc_id,
        "title": (EXPLORE_TITLE_PREFIX + str(turn) + " \u2014 " + goal)[:250],
        "verdict": "",
        "status": str(result.get("status") or ""),
        "reason": str(result.get("reason") or "")[:1200],
        "trace": list(result.get("trace") or []),
        "escapes": 0,
        "finding": " ".join(str(finding or "").split())[:600],
        "started": now,
        "updated": now,
        # Shaped like `case_runner._evidence_record`'s output so the report's
        # evidence join reads a record rather than a missing key.
        "evidence": {
            "profile": None,
            "clock_offset_ms": None,
            "pid": None,
            "skipped": "an exploratory turn takes no app-log slice",
            "slices": 0,
            "slices_written": [],
        },
    }
    try:
        run_store.write_case(run_id, tc_id, written)
    except Exception:  # pragma: no cover - defensive
        logger.warning("mobile.session: could not checkpoint an explore turn")
    return written


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


async def submit(
    run_id: str,
    tc_id: str,
    raw_script: object,
    *,
    session_token: str = "",
    tester_input: str = "",
    tester_input_field: str = "",
) -> dict:
    """Replay one answered packet and return the verdict plus the NEXT packet.

    The tester's value is put on ``Context.tester_inputs`` and nowhere else: it
    is not returned, not checkpointed, not logged and not part of any dict this
    function hands back. The FIELD NAME travels; the value does not.
    """
    try:
        resolved = resolve(run_id, session_token)
        if resolved.get("error"):
            return resolved
        body = resolved["content"] or {}
        ctx = context_for(body)
        field = str(tester_input_field or "").strip()[:80]
        if field and str(tester_input or ""):
            ctx.tester_inputs = {field: str(tester_input)}
        if str(body.get("lane")) == LANE_EXPLORE:
            return await _submit_explore(run_id, raw_script, ctx, body)
        loaded = load_case(run_id, tc_id)
        if loaded.get("error"):
            return loaded
        result = await case_runner.submit_case(
            run_id, loaded["content"], ctx, raw_script
        )
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
        return {
            "error": None,
            "content": {
                "state": state,
                "case": content,
                "packet": content.get("packet"),
                "next": follow,
                "field": field,
                "resolved": body,
            },
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.submit failed")
        return {"error": str(exc), "content": None}


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
    parsed = actions_mod.parse_script(payload.get("actions"))
    if parsed.get("error"):
        return {
            "error": None,
            "content": {
                "state": STATE_RUNNING,
                "case": {"status": case_runner.NEEDS_MODEL, "reason": parsed["error"]},
                "packet": None,
                "next": {},
                "field": "",
                "resolved": resolved,
            },
        }
    replayed = await executor.replay(parsed["content"], ctx)
    if replayed.get("error"):
        return replayed
    outcome = replayed.get("content") or {}
    folded = explore_runner.apply_turn_result(resolved.get("explore") or {}, payload)
    if folded.get("error"):
        return folded
    turn = folded.get("content") or {}
    # The turn just replayed, checkpointed as a case. ``resolved["explore"]``
    # carries the turn NUMBER this submit answered (``next_turn`` incremented
    # and persisted it before the packet went out), so the id is the turn's own
    # and is the same on a resubmit.
    before = resolved.get("explore") or {}
    finding = _latest_finding(turn.get("state"))
    checkpoint = _checkpoint_explore_turn(run_id, before, outcome, finding)
    _persist_explore(
        run_id,
        turn.get("state"),
        planned=_explore_planned_case(checkpoint["tc_id"], before, finding),
    )
    if str(turn.get("status") or "") != explore_runner.RUNNING:
        await _finish_evidence(run_id, resolved)
    return {
        "error": None,
        "content": {
            "state": (
                STATE_REPORT
                if str(turn.get("status") or "") != explore_runner.RUNNING
                else STATE_RUNNING
            ),
            "case": {
                "tc_id": checkpoint["tc_id"],
                "title": checkpoint["title"],
                "status": str(outcome.get("status") or ""),
                "verdict": "",
                "reason": str(outcome.get("reason") or ""),
                "trace": list(outcome.get("trace") or []),
            },
            "packet": None,
            "next": {},
            "notice": str(turn.get("notice") or ""),
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
        return run_store.redact(detail)
    except Exception:  # pragma: no cover - defensive
        logger.warning("mobile.session.audit_detail failed", exc_info=True)
        return {}


def provision_progress() -> dict:
    return provisioner.read_progress()


def start_provisioning() -> dict:
    """Kick the DETACHED provisioner. Nothing downloads inside this process."""
    return provisioner.start_detached()


def provision_plan() -> dict:
    return provisioner.plan()


def screen_of(dump: object, activity: str = "") -> dict:
    """Prune a dump. Here so a handler never touches raw XML itself."""
    return perception.prune(dump, activity)
