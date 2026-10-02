"""A fast screen dump through the qa-ime AccessibilityService, with uiautomator as the fallback.

**Why.** ``uiautomator dump`` costs seconds a look; the service answers the same question
from inside the device. The reply is uiautomator-shaped XML, so ``perception.prune`` (and the
destructive guard downstream of it) consume it unchanged: this module changes WHERE the XML
comes from, never which node is judged. ``enable`` refuses the provider unless a parity check
against a real uiautomator dump agrees.

**One window.** The service dumps only the ACTIVE window (``rootInActiveWindow``), which is
what ``uiautomator dump`` does by default. That default is ASSUMED, not device-verified; the
parity check is what catches a device where it is not true.

**Modelled on ``animations``.** A per-run record is written BEFORE the setting changes, the
restore reads only that record, and a stale sweep gives back what a crashed run left behind.
Every public function returns ``{"error", "content"}`` and never raises.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import os
import re
import time
import zlib

from tools.mobile import adb, ime, ime_nonce, ime_session, perception, run_store

logger = logging.getLogger(__name__)

RECORD_FILE = "a11y.json"
SETTING = "enabled_accessibility_services"
MASTER = "accessibility_enabled"

#: Seconds one DUMP broadcast may take before the run falls back to uiautomator.
A11Y_DUMP_TIMEOUT_S = 8

#: Characters of base64 one DUMP reply may carry (the binder limit is near 1 MB).
A11Y_MAX_REPLY_CHARS = 1000000

#: Consecutive failed fast dumps before the provider is dropped for the run.
A11Y_MAX_CONSECUTIVE_FAILURES = 3

#: Polls waiting for the service to connect after the setting changed.
A11Y_CONNECT_MAX_POLLS = 10
A11Y_CONNECT_POLL_INTERVAL_S = 0.5

#: The longest enabled_accessibility_services value this module will rewrite.
A11Y_MAX_SERVICES_VALUE_CHARS = 1000

#: Share of nodes (by class, id, bounds) both dumps must have in common.
A11Y_PARITY_OVERLAP_FRACTION = 0.9

#: Refusal codes that mean "not ready yet" while the service connects.
_NOT_READY = ("no_service", "no_window")

_VALUE_RE = re.compile("[A-Za-z0-9_.$/:-]{1,%d}" % A11Y_MAX_SERVICES_VALUE_CHARS)

_RUNS: dict = {}
_FAILS: dict = {}


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def component() -> str:
    """``package/package.QaA11yService`` for the pinned APK, or ``""``."""
    content = (ime.manifest() or {}).get("content") or {}
    package = str(content.get("package") or "")
    return package + "/" + package + ".QaA11yService" if package else ""


def _record_path(run_id: str):
    return run_store.run_path(str(run_id)) / RECORD_FILE


def read_record(run_id: str) -> dict:
    try:
        body = run_store._read_json(_record_path(run_id))
    except Exception:
        logger.exception("mobile.a11y.read_record failed")
        return {}
    return body if isinstance(body, dict) else {}


def write_record(run_id: str, body: dict) -> dict:
    try:
        run_store._write_json(_record_path(run_id), dict(body or {}))
        return {"error": None, "content": dict(body or {})}
    except Exception as exc:
        logger.exception("mobile.a11y.write_record failed")
        return {"error": str(exc), "content": None}


def clear_record(run_id: str) -> None:
    try:
        target = _record_path(run_id)
        if target.is_file():
            target.unlink()
    except Exception:
        logger.exception("mobile.a11y.clear_record failed")


async def _sh(serial: str, command: str) -> dict:
    result = await adb.shell(str(serial), [command])
    if result.get("error"):
        return result
    body = result.get("content") or {}
    if int(body.get("rc") or 0) != 0:
        detail = (
            str(body.get("err") or "").strip() or str(body.get("out") or "").strip()
        )
        return {
            "error": "adb shell settings failed ("
            + (detail[:200] or "exit " + str(body.get("rc")))
            + ")",
            "content": None,
        }
    return {"error": None, "content": str(body.get("out") or "")}


def _norm(value: object) -> str:
    text = str(value or "").strip()
    return "" if text == "null" else text[: A11Y_MAX_SERVICES_VALUE_CHARS + 1]


def _items(value: str) -> list:
    return [part for part in str(value).split(":") if part]


async def read_state(serial: str) -> dict:
    """``(services, master)`` as device strings, in ONE shell call."""
    result = await _sh(
        serial,
        "settings get secure " + SETTING + "; settings get secure " + MASTER,
    )
    if result.get("error"):
        return result
    lines = str(result["content"]).splitlines()
    lines = (lines + ["", ""])[:2] if len(lines) < 2 else lines[:2]
    return {"error": None, "content": (_norm(lines[0]), _norm(lines[1]))}


def _restore_command(services: str, master):
    """The shell line giving the recorded values back, or ``None`` when unsafe.

    A *master* of ``None`` leaves ``accessibility_enabled`` alone: another service is
    enabled and the switch is theirs.
    """
    if services and not _VALUE_RE.fullmatch(services):
        return None
    if services:
        first = "settings put secure " + SETTING + " '" + services + "'"
    else:
        first = "settings delete secure " + SETTING
    if master is None:
        return first
    if master in ("0", "1"):
        second = "settings put secure " + MASTER + " " + master
    else:
        second = "settings delete secure " + MASTER
    return first + " && " + second


def decode(b64: str) -> dict:
    """``base64(gzip(xml))`` -> XML text. Bounded: a bomb is an error, never truncated."""
    try:
        if len(b64) > A11Y_MAX_REPLY_CHARS:
            return {"error": "the a11y reply exceeds its cap", "content": None}
        raw = base64.b64decode(b64, validate=True)
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        out = inflater.decompress(raw, adb.MAX_DUMP_BYTES + 1)
        if len(out) > adb.MAX_DUMP_BYTES or inflater.unconsumed_tail:
            return {
                "error": "the a11y dump exceeds the "
                + str(adb.MAX_DUMP_BYTES)
                + " byte cap",
                "content": None,
            }
        text = out.decode("utf-8", errors="replace")
        if not text.lstrip().startswith("<"):
            return {"error": "the a11y reply was not XML", "content": None}
        return {"error": None, "content": text}
    except (binascii.Error, zlib.error, ValueError) as exc:
        return {
            "error": "the a11y reply could not be decoded (" + str(exc)[:80] + ")",
            "content": None,
        }


async def fetch_dump(serial: str) -> dict:
    """One DUMP broadcast -> ``{"error", "content": xml}``. Never raises."""
    try:
        if not ime_nonce.get(serial):
            return {"error": "the QA keyboard is not armed", "content": None}
        actions = ((ime.manifest() or {}).get("content") or {}).get("actions") or {}
        action = str(actions.get("dump") or "")
        if not action:
            return {"error": "no dump action is pinned", "content": None}
        sent = await asyncio.wait_for(
            ime.send(serial, action), timeout=A11Y_DUMP_TIMEOUT_S
        )
        if sent.get("error"):
            return sent
        reply = ime.reply_of(sent)
        if int(reply.get("result", -1)) == ime_nonce.REPLY_REFUSED:
            return {
                "error": "refused: " + str(reply.get("refusal") or "unknown"),
                "content": None,
            }
        if int(reply.get("result", -1)) != 1 or not reply.get("dump"):
            return {"error": "the a11y service did not answer", "content": None}
        return decode(str(reply["dump"]))
    except Exception as exc:
        logger.debug("mobile.a11y.fetch_dump failed", exc_info=True)
        return {
            "error": "a11y dump failed (" + (str(exc)[:80] or type(exc).__name__) + ")",
            "content": None,
        }


async def provider(serial: str) -> dict:
    """The dump provider ``adb.uiautomator_dump`` consults; drops itself after repeated failures."""
    got = await fetch_dump(serial)
    if got.get("error"):
        _FAILS[serial] = _FAILS.get(serial, 0) + 1
        if _FAILS[serial] >= A11Y_MAX_CONSECUTIVE_FAILURES:
            adb.clear_dump_provider(serial)
        return got
    _FAILS[serial] = 0
    return got


def _keyed(xml: str):
    # Both dumps are of the same screen, so they share whatever frame prune derives.
    pruned = perception.prune(xml, display=None)
    if not isinstance(pruned, dict) or pruned.get("error"):
        return None
    body = pruned.get("content") if isinstance(pruned.get("content"), dict) else pruned
    out: dict = {}
    for element in body.get("elements") or []:
        if not isinstance(element, dict):
            continue
        key = (
            str(element.get("cls") or ""),
            str(element.get("rid") or ""),
            tuple(element.get("bounds") or []),
        )
        out[key] = (
            str(element.get("text") or ""),
            str(element.get("desc") or ""),
            bool(element.get("clickable")),
            bool(element.get("editable")),
            bool(element.get("secure")),
        )
    return out


def compare(fast_xml: str, slow_xml: str) -> dict:
    """Refuse the fast dump unless it describes the same nodes as uiautomator."""
    fast, slow = _keyed(fast_xml), _keyed(slow_xml)
    if fast is None or slow is None:
        return {
            "error": "a11y parity check: a dump could not be parsed",
            "content": None,
        }
    union = set(fast) | set(slow)
    shared = set(fast) & set(slow)
    if not union or len(shared) / len(union) < A11Y_PARITY_OVERLAP_FRACTION:
        return {
            "error": "a11y parity check: the two dumps share too few nodes",
            "content": None,
        }
    differing = sum(1 for key in shared if fast[key] != slow[key])
    # Zero tolerance, not a cap: the destructive guard must see the same node either way.
    if differing:
        return {
            "error": "a11y parity check: "
            + str(differing)
            + " node(s) differ from uiautomator",
            "content": None,
        }
    return {"error": None, "content": {"shared": len(shared)}}


async def _await_connected(serial: str) -> dict:
    last: dict = {"error": "the a11y service did not connect", "content": None}
    for poll in range(A11Y_CONNECT_MAX_POLLS):
        got = await fetch_dump(serial)
        if not got.get("error"):
            return got
        last = got
        if not any(code in str(got["error"]) for code in _NOT_READY):
            return got
        if poll < A11Y_CONNECT_MAX_POLLS - 1:
            await _sleep(A11Y_CONNECT_POLL_INTERVAL_S)
    return last


def append_service(items: list, ours: str) -> str:
    """The ``enabled_accessibility_services`` value with *ours* added after the tester's."""
    return ":".join(list(items) + [ours])


async def enable(serial: str, run_id: str) -> dict:
    """Turn the service on for *run_id*, verify parity, register the provider.

    A legacy APK (no nonce, protocol < 2) is a silent no-op. Any failure restores what was
    changed and returns an error; the caller falls back to uiautomator.
    """
    try:
        if ime_nonce.protocol(serial) < ime_nonce.PROTOCOL_NONCE or not ime_nonce.get(
            serial
        ):
            return {"error": None, "content": {"enabled": False, "reason": "legacy"}}
        ours = component()
        if not ours:
            return {"error": "no accessibility component is pinned", "content": None}
        state = await read_state(serial)
        if state.get("error"):
            return state
        services, master = state["content"]
        items = _items(services)
        already = ours in items
        if not already:
            if services and not _VALUE_RE.fullmatch(services):
                return {
                    "error": "the enabled accessibility services value cannot be restored safely",
                    "content": None,
                }
            written = append_service(items, ours)
            recorded = write_record(
                run_id,
                {
                    "serial": str(serial),
                    "previous_services": services,
                    "previous_master": master,
                    "written": written,
                    "pid": os.getpid(),
                    "recorded_at": time.time(),
                },
            )
            if recorded.get("error"):
                return recorded
            put = await _sh(
                serial,
                "settings put secure "
                + SETTING
                + " '"
                + written
                + "' && settings put secure "
                + MASTER
                + " 1",
            )
            if put.get("error"):
                await restore(run_id)
                return put
        _RUNS[str(run_id)] = str(serial)
        connected = await _await_connected(serial)
        if connected.get("error"):
            await restore(run_id)
            return connected
        from tools.mobile import executor  # lazy: executor's import graph is wide

        slow = await executor.dump_raw(serial, use_provider=False)
        checked = (
            slow
            if slow.get("error")
            else compare(str(connected["content"]), str(slow["content"]))
        )
        if checked.get("error"):
            await restore(run_id)
            return checked
        _FAILS[str(serial)] = 0
        adb.set_dump_provider(serial, provider)
        return {"error": None, "content": {"enabled": True, "changed": not already}}
    except Exception as exc:
        logger.exception("mobile.a11y.enable failed")
        return {"error": str(exc), "content": None}


async def _give_back(serial: str, record: dict) -> dict:
    state = await read_state(serial)
    if state.get("error"):
        return state
    current = state["content"][0]
    ours = component()
    if current == _norm(record.get("written")):
        services = _norm(record.get("previous_services"))
        master = _norm(record.get("previous_master"))
    else:
        services = ":".join(part for part in _items(current) if part != ours)
        # Another service is enabled now: the master switch is theirs to keep on, so it is
        # not put back to the value from before this run (it may have been "0").
        master = None if services else _norm(record.get("previous_master"))
    command = _restore_command(services, master)
    if command is None:
        return {
            "error": "the recorded accessibility value is unsafe to write back",
            "content": None,
        }
    return await _sh(serial, command)


async def restore(run_id: str) -> dict:
    """Disable the service and put the recorded values back. Takes NO serial.

    A failed restore keeps the record, marked ``ended``, so the next run's sweep retries.
    """
    try:
        remembered = _RUNS.pop(str(run_id), "")
        record = read_record(run_id)
        serial = str(record.get("serial") or remembered)
        if serial:
            adb.clear_dump_provider(serial)
            _FAILS.pop(serial, None)
        if not record:
            return {"error": None, "content": {"restored": False, "detail": ""}}
        if not serial:
            clear_record(run_id)
            return {
                "error": None,
                "content": {
                    "restored": False,
                    "detail": "the a11y record named no device",
                },
            }
        result = await _give_back(serial, record)
        if result.get("error"):
            write_record(run_id, {**record, "ended": True})
            return result
        clear_record(run_id)
        return {
            "error": None,
            "content": {"restored": True, "detail": "accessibility services restored"},
        }
    except Exception as exc:
        logger.exception("mobile.a11y.restore failed")
        return {"error": str(exc), "content": None}


def stale_records(serial: str, *, skip_run_id: str = "") -> list:
    """Records from OTHER runs that still claim to have changed *serial*."""
    out: list = []
    try:
        wanted = str(serial or "")
        if not wanted:
            return out
        listed = run_store.list_runs(ime_session.IME_STALE_SCAN_RUNS, gc=False) or {}
        for row in listed.get("content") or []:
            run_id = str((row or {}).get("run_id") or "")
            if not run_id or run_id == str(skip_run_id or ""):
                continue
            record = read_record(run_id)
            if record and str(record.get("serial") or "") == wanted:
                out.append({"run_id": run_id, **record})
    except Exception:
        logger.exception("mobile.a11y.stale_records failed")
    return out


async def restore_stale(serial: str, *, skip_run_id: str = "") -> dict:
    """Give back accessibility values a crashed run left on *serial* (fail-closed, as animations)."""
    try:
        from tools.mobile import locks as mobile_locks

        found = stale_records(serial, skip_run_id=skip_run_id)
        if not found:
            return {
                "error": None,
                "content": {"restored": False, "detail": "", "runs": []},
            }
        rows = mobile_locks.device_holders([str(serial)]) or []
        row = rows[0] if rows else {}
        held_by_another = bool(row.get("held")) and str(row.get("owner") or "") != str(
            skip_run_id or ""
        )
        undead = [
            r["run_id"]
            for r in found
            if r.get("ended") is not True
            and not ime_session.pid_is_provably_dead(r.get("pid"))
        ]
        runs = [r["run_id"] for r in found]
        if not rows or row.get("error") or held_by_another or undead:
            return {
                "error": None,
                "content": {"restored": False, "detail": "", "runs": undead or runs},
            }
        found.sort(key=lambda r: float(r.get("recorded_at") or 0.0))
        result = await _give_back(serial, found[0])
        if result.get("error"):
            return {
                "error": None,
                "content": {
                    "restored": False,
                    "detail": "could not restore the accessibility services an earlier run left on ("
                    + str(result["error"])[:160]
                    + "). It will be tried again next time.",
                    "runs": runs,
                },
            }
        for record in found:
            clear_record(str(record.get("run_id") or ""))
        return {
            "error": None,
            "content": {
                "restored": True,
                "detail": "restored the accessibility services an earlier run left on",
                "runs": runs,
            },
        }
    except Exception as exc:
        logger.exception("mobile.a11y.restore_stale failed")
        return {"error": str(exc), "content": None}
