"""The QA IME, made ready for a run and handed back afterwards.

**Why this module exists at all.** `ime.py` has had `remember_previous` and
`restore_previous` since the keyboard was pinned, and until now NOTHING called
either: the lane never selected the QA IME automatically, so a tester whose own
script used ADBKeyBoard typed into a keyboard whose receiver never registers on
API 35 and got silence. Measured on a second Mac, 2026-09-17. This module is the
first caller of both, and the sequence lives in one place so the order -- remember
BEFORE anything changes -- cannot be got wrong by a second caller written later.

**It takes no new setting.** `ime.ensure_apk` and `ime.install` already refuse
without `QA_MOBILE_RUN_ENABLED`, and `apply=true` still gates the run. Per
CLAUDE.md's flag policy, an improvement ships ON with no flag of its own; the
kill-switch and `apply` remain the only two gates.

House rules obeyed: no `print`, and every public function returns
``{"error", "content"}`` rather than raising.
"""

from __future__ import annotations

import asyncio
import logging
import time

from tools.mobile import ime, run_store

logger = logging.getLogger(__name__)

#: The per-run record of whose keyboard we displaced.
#:
#: Beside the run's other artifacts and written through ``run_store``'s own
#: writer, so it inherits the redact pass and the atomic write everything else
#: in a run folder gets rather than growing a second writer with its own rules.
RECORD_FILE = "ime.json"

#: How long the whole ensure sequence may take before it is reported as failed.
#:
#: It is an install plus three shell round trips on a device that may be
#: booting. The constraint is a TESTER's patience at the first type op of a run,
#: not the device's speed: past this the honest answer is "typing is not ready",
#: because a run that hangs here looks to a tester exactly like a run that hung
#: in their app.
IME_READY_TIMEOUT_S = 120.0

#: How long the read-back after a type op may take.
#:
#: One broadcast and one reply from an app already installed and selected. The
#: constraint is that this runs after EVERY type op, so it is paid per op rather
#: than per run and must not become the thing that makes typing feel slow.
IME_VERIFY_TIMEOUT_S = 15.0


def _record_path(run_id: str):
    return run_store.run_path(str(run_id)) / RECORD_FILE


def read_record(run_id: str) -> dict:
    """The run's IME record, or ``{}``. Never raises."""
    try:
        body = run_store._read_json(_record_path(run_id))
    except Exception:  # pragma: no cover - _read_json already swallows
        logger.exception("mobile.ime_session.read_record failed")
        return {}
    return body if isinstance(body, dict) else {}


def write_record(run_id: str, body: dict) -> dict:
    """Write the run's IME record through run_store's own writer."""
    try:
        run_store._write_json(_record_path(run_id), dict(body or {}))
        return {"error": None, "content": dict(body or {})}
    except Exception as exc:
        logger.exception("mobile.ime_session.write_record failed")
        return {"error": str(exc), "content": None}


def clear_record(run_id: str) -> None:
    """Drop the record. A missing file is success: the point is that it is gone."""
    try:
        target = _record_path(run_id)
        if target.is_file():
            target.unlink()
    except Exception:
        logger.exception("mobile.ime_session.clear_record failed")


async def state(serial: str) -> dict:
    """``{"installed", "selected", "current"}`` for one device. Never raises."""
    try:
        resolved = ime.manifest()
        if resolved.get("error"):
            return resolved
        ours = str((resolved["content"] or {}).get("ime_id") or "")
        present = await ime.installed(serial)
        if present.get("error"):
            return present
        current = await ime.current_ime(serial)
        if current.get("error"):
            return current
        now = str(current.get("content") or "")
        return {
            "error": None,
            "content": {
                "installed": bool((present.get("content") or {}).get("installed")),
                # By COMPONENT IDENTITY, not spelling. A device stores the
                # shorthand `pkg/.Class` while the manifest pins the expanded
                # form, and `==` answers "that is not our keyboard" about our
                # own keyboard -- the defect `ime.same_component` exists for.
                "selected": ime.same_component(now, ours),
                "current": now,
            },
        }
    except Exception as exc:
        logger.exception("mobile.ime_session.state failed")
        return {"error": str(exc), "content": None}


async def ensure_ready(serial: str, run_id: str) -> dict:
    """Make the QA IME the active keyboard for this run, once, within a bound.

    Bounded HERE rather than by the caller: this is an install plus three shell
    round trips on a device that may still be booting, and a caller that forgot
    the timeout would hang with nothing to stop it. A timeout is reported as a
    refusal naming the device, never as a success -- typing into a keyboard that
    never became active is the exact silence this whole change exists to end.
    """
    try:
        return await asyncio.wait_for(
            _ensure_ready(serial, run_id), timeout=IME_READY_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        return {
            "error": (
                "The QA input method did not become ready on "
                + str(serial)[:64]
                + " within "
                + str(int(IME_READY_TIMEOUT_S))
                + "s. Nothing was typed."
            ),
            "content": None,
        }


async def _ensure_ready(serial: str, run_id: str) -> dict:
    """The sequence itself. See :func:`ensure_ready` for the bound.

    Idempotent: a device already installed-and-selected is reported as ready
    and nothing is written or changed. Otherwise the tester's current keyboard
    is remembered FIRST -- before anything is installed, enabled or selected --
    because a remember that runs after the change records our own keyboard as
    the thing to restore, which is how a tester's phone keeps a keyboard they
    never chose.
    """
    try:
        now = await state(serial)
        if now.get("error"):
            return now
        body = now.get("content") or {}
        if body.get("installed") and body.get("selected"):
            return {
                "error": None,
                "content": {
                    "ready": True,
                    "changed": False,
                    "detail": "the QA input method was already installed and selected",
                },
            }

        # REMEMBER FIRST. Everything below this line changes the device.
        if not read_record(run_id):
            remembered = await ime.remember_previous(serial)
            if remembered.get("error"):
                return remembered
            kept = remembered.get("content") or {}
            written = write_record(
                run_id,
                {
                    "serial": str(serial),
                    "previous": str(kept.get("previous") or ""),
                    "was_ours": bool(kept.get("was_ours")),
                    "selected_at": time.time(),
                },
            )
            if written.get("error"):
                # A record we could not write is a restore we could not perform,
                # so this refuses rather than displacing a keyboard it has no way
                # to give back.
                return {
                    "error": (
                        "Refusing to select the QA input method: its record could "
                        "not be written, so the keyboard you have now could not be "
                        "restored afterwards (" + str(written["error"])[:120] + ")."
                    ),
                    "content": None,
                }

        if not body.get("installed"):
            installed = await ime.install(serial)
            if installed.get("error"):
                return installed
        enabled = await ime.enable(serial)
        if enabled.get("error"):
            return enabled
        chosen = await ime.select(serial)
        if chosen.get("error"):
            return chosen

        after = await state(serial)
        if after.get("error"):
            return after
        settled = after.get("content") or {}
        if not settled.get("selected"):
            return {
                "error": (
                    "The QA input method was installed and enabled but did not "
                    "become the active keyboard; typing would go to "
                    + (str(settled.get("current") or "no keyboard")[:120])
                    + ". Nothing was typed."
                ),
                "content": None,
            }
        return {
            "error": None,
            "content": {
                "ready": True,
                "changed": True,
                "detail": "selected the QA input method",
            },
        }
    except Exception as exc:
        logger.exception("mobile.ime_session.ensure_ready failed")
        return {"error": str(exc), "content": None}


async def restore(run_id: str) -> dict:
    """Give the tester's keyboard back. Takes NO serial -- it reads its own record.

    That is what lets one chokepoint call it: ``session.finish_device`` knows the
    run's owner and nothing else, and a restore that demanded a serial would push
    the job of remembering one onto every caller.

    A no-op is REPORTED, never silent, and there are four of them: no record (the
    run never typed), a run folder that is gone, a blank previous, and a previous
    that is our own keyboard. A tester cannot otherwise tell "nothing needed
    doing" from "the restore failed quietly".
    """
    try:
        record = read_record(run_id)
        if not record:
            return {
                "error": None,
                "content": {
                    "restored": False,
                    "detail": (
                        "No input method was changed for this run, so none was "
                        "restored."
                    ),
                },
            }
        serial = str(record.get("serial") or "")
        previous = str(record.get("previous") or "")
        if not serial:
            clear_record(run_id)
            return {
                "error": None,
                "content": {
                    "restored": False,
                    "detail": "the run's IME record named no device, so nothing was restored",
                },
            }
        result = await ime.restore_previous(serial, previous)
        # The record is dropped either way: a restore that failed will not
        # succeed on a second attempt from a run that has already ended, and a
        # stale record would make the NEXT run think it had already remembered.
        clear_record(run_id)
        if result.get("error"):
            return result
        return result
    except Exception as exc:
        logger.exception("mobile.ime_session.restore failed")
        return {"error": str(exc), "content": None}


async def read_back(serial: str) -> dict:
    """The field's text after a type op, within a bound.

    Separate from ``ime.query`` so the cap has an owner: this runs after EVERY
    type op, so an unbounded read would make one unresponsive device stall a
    whole run one op at a time. A timeout is "could not verify", never "the text
    matched" -- a verification that fails open verifies nothing.
    """
    try:
        return await asyncio.wait_for(ime.query(serial), timeout=IME_VERIFY_TIMEOUT_S)
    except asyncio.TimeoutError:
        return {
            "error": (
                "Could not read the field back from "
                + str(serial)[:64]
                + " within "
                + str(int(IME_VERIFY_TIMEOUT_S))
                + "s, so what was typed could not be verified."
            ),
            "content": None,
        }


def verify_typed(field_text: object, sent: object) -> dict:
    """Did the field end up holding what was typed?

    **Ends-with, not equality.** A field may already hold text -- typing into a
    non-empty field is an append, and equality would fail every one of them.
    What this can honestly assert is that what was sent is now at the end.

    Returns ``{"ok", "detail"}``. A blank *sent* is vacuous and is reported as
    not verified rather than as a pass, because "we asked for nothing and found
    nothing" is not evidence the keyboard works.
    """
    wanted = str(sent or "")
    got = str(field_text or "")
    if not wanted:
        return {"ok": False, "detail": "nothing was sent, so nothing was verified"}
    if got.endswith(wanted):
        return {"ok": True, "detail": "the field ends with the text that was sent"}
    return {
        "ok": False,
        "detail": (
            "the field does not end with the text that was sent -- it holds "
            + (repr(got[-120:]) if got else "nothing")
            + ". The keyboard may not be the active one."
        ),
    }
