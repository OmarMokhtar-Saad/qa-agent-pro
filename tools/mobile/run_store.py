"""Per-run state on disk: manifest, per-case checkpoints, and the lease.

Three deliberate choices.

**Atomic writes.** Every write is tmp-file + ``os.replace``, so a run killed
mid-write leaves the previous good file rather than half a JSON document. A
resumable run whose checkpoint can be truncated is not resumable.

**One screen library per run.** ``screens/<screen_id>.json`` holds the PRUNED
screen a step saw, keyed by the id every trace entry already carries. It exists
because a trace's ``before_screen_id`` / ``after_screen_id`` were identifiers
with nothing behind them -- no element ``bounds`` had ever reached disk, so the
report's wireframes were unbuildable from this store. Keying on ``screen_id``
makes dedup free (it is stable across a one-pixel scroll), so a 200-case run
stores each distinct screen ONCE rather than once per case.

**Redaction at the WRITE, not at the reader.** :func:`redact` runs inside every
write path. It masks two things: any value under a key in
``SECRET_VALUE_KEYS`` inside an object marked ``secret: true``, and any value
under a key in ``ALWAYS_MASKED_KEYS`` wherever it appears. The first is the
contract the executor uses; the second exists because the first is
MARK-dependent, and a caller that forgets to MARK would otherwise write a
credential in clear -- which a planted probe did.

It is still not a value scrubber: a credential stored under an unrecognised key
name with no marker WILL reach disk, so a new writer of tester-supplied values
must mark it. ``tools/audit_log.record_event`` writes its ``detail`` verbatim with no
redaction hook, so the same helper is what a handler must pass through before
auditing.

**An injected clock.** Every lease function takes ``now``. Lease takeover is
state-machine logic with a staleness threshold; asserting it against wall-clock
sleeps would put the test below its own noise floor and make it both slow and
flaky. Nothing here reads ``time.time()`` unless the caller declines to say
when "now" is.

**Where mutual exclusion actually lives.** The lease is an ADVISORY record of
"which chat is driving run X", and :func:`acquire_lease` is read-decide-write:
``_write_json`` makes each individual WRITE atomic, but the sequence is not, so
two sessions racing the same stale lease can both decide to take it.
:func:`acquire_lease` narrows that with a compare-after-swap (it re-reads and
reports ``reason="lost_race"`` when another session already won) and any
survivor of the residual window is told on its next :func:`touch_lease`. The
real mutual exclusion over the physical device is NOT here -- it is
``tools/mobile/locks.py``, whose ``O_CREAT|O_EXCL`` emulator lock is what stops
two processes driving one emulator. This module deliberately does not import
``locks``: a run record and a device lock have different lifetimes, and the
handler that owns both is Phase 3's. A reader of this file alone should know
that the boundary is over there.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from pathlib import Path

from config.settings import settings
from tools.mobile import paths

logger = logging.getLogger(__name__)

#: What a secret value is replaced with, everywhere.
SECRET_MASK = "***"

#: Keys whose value is masked when the containing object is marked secret.
#: Masked wherever they appear, with or without a ``secret`` marker, because a
#: key with one of these names holds a credential by definition. This is the
#: defence-in-depth half of :func:`redact`: the marker-based half below is the
#: contract, and this is what survives a caller forgetting it.
ALWAYS_MASKED_KEYS: frozenset[str] = frozenset(
    {"password", "passcode", "pin", "otp", "secret_value", "credential"}
)

SECRET_VALUE_KEYS: tuple[str, ...] = (
    "text",
    "value",
    "tester_input",
    "input",
    "password",
    "secret_value",
)

#: A lease whose heartbeat is older than this is up for grabs.
LEASE_STALE_S = 120

#: A run whose newest activity is older than this is collected when the run list
#: is read. Seven days: long enough that a tester who was away for a week still
#: finds their results, short enough that a machine does not accumulate run
#: directories forever.
STALE_RUN_S = 7 * 24 * 3600

#: Named here because the GC refusal has to print it.
FLAG_NAME = "QA_MOBILE_RUN_ENABLED"

LEASE_FILE = "lease.json"
MANIFEST_FILE = "manifest.json"
CASES_DIR = "cases"
SCREENS_DIR = "screens"

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_TC_ID_RE = re.compile(r"^TC-\d{3,6}$")
_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

#: Lease states a caller branches on.
HELD = "held"
TAKEN_OVER = "taken_over"
NONE = "none"


def _now(value: float | None) -> float:
    return float(value) if value is not None else time.time()


def valid_run_id(value: object) -> bool:
    return bool(isinstance(value, str) and _RUN_ID_RE.match(value))


#: The SHAPE :func:`session.mint_run_id` produces: ``mrun-YYYYmmdd-HHMMSS-hex6``.
#: Deliberately separate from :func:`valid_run_id`, which answers a DIFFERENT
#: question -- "is this safe to use as a path segment" -- and therefore accepts
#: every single-token status string in this lane (`handoff_failed`, `not_held`,
#: `already_held`). Using the safety check as an identity check put
#: `run_id="handoff_failed"` one key name away from a tester's screen, and it is
#: the same conflation that made a bare `run_id` a bad lock-owner label.
_RUN_ID_SHAPE_RE = re.compile(r"^mrun-\d{8}-\d{6}-[0-9a-f]{6}$")


def looks_like_a_run_id(value: object) -> bool:
    """True when *value* has the shape of a run id this lane MINTS.

    For deciding whether a string may be shown to a tester as something they can
    pass back as ``run_id``. :func:`valid_run_id` remains the right check for
    "may this reach the filesystem", and a real run id satisfies both.
    """
    return bool(isinstance(value, str) and _RUN_ID_SHAPE_RE.match(value))


def valid_tc_id(value: object) -> bool:
    return bool(isinstance(value, str) and _TC_ID_RE.match(value))


def _is_secret(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


def redact(obj: object) -> object:
    """Recursively mask the value of anything marked ``secret``.

    Applied on every write in this module. Structure is preserved -- the report
    still shows that a credential was typed into a named field, which is what a
    tester needs, without showing the credential.
    """
    if isinstance(obj, dict):
        out: dict = {key: redact(value) for key, value in obj.items()}
        if _is_secret(out.get("secret")):
            for key in SECRET_VALUE_KEYS:
                if key in out:
                    out[key] = SECRET_MASK
        # Mark-INDEPENDENT. The block above only fires inside an object that
        # declared itself secret, so a caller that forgets the marker used to
        # put the value on disk in clear. These key names are credentials by
        # name wherever they appear.
        for key in list(out):
            if str(key).lower() in ALWAYS_MASKED_KEYS and isinstance(
                out[key], (str, bytes)
            ):
                out[key] = SECRET_MASK
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(item) for item in obj]
    return obj


def _write_json(target: Path, payload: object) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    text = json.dumps(redact(payload), indent=2, sort_keys=True, default=str)
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, target)


def _read_json(target: Path) -> object | None:
    try:
        if not target.is_file():
            return None
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("mobile.run_store: unreadable %s: %s", target, exc)
        return None


def run_path(run_id: str) -> Path:
    return paths.run_dir(run_id)


def create_run(run_id: str, manifest: dict) -> dict:
    """Create ``runs/<run_id>/`` and write its manifest. Idempotent."""
    try:
        if not valid_run_id(run_id):
            return {
                "error": "Refusing to use " + repr(str(run_id)[:40]) + " as a run id.",
                "content": None,
            }
        root = run_path(run_id)
        (root / CASES_DIR).mkdir(parents=True, exist_ok=True)
        payload = dict(manifest or {})
        payload.setdefault("run_id", run_id)
        payload.setdefault("created", time.time())
        _write_json(root / MANIFEST_FILE, payload)
        return {"error": None, "content": {"run_id": run_id, "path": str(root)}}
    except Exception as exc:
        logger.exception("mobile.run_store.create_run failed")
        return {"error": str(exc), "content": None}


def write_manifest(run_id: str, manifest: dict) -> dict:
    try:
        if not valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        _write_json(run_path(run_id) / MANIFEST_FILE, dict(manifest or {}))
        return {"error": None, "content": {"run_id": run_id}}
    except Exception as exc:
        logger.exception("mobile.run_store.write_manifest failed")
        return {"error": str(exc), "content": None}


def read_manifest(run_id: str) -> dict:
    try:
        if not valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        return {"error": None, "content": _read_json(run_path(run_id) / MANIFEST_FILE)}
    except Exception as exc:
        logger.exception("mobile.run_store.read_manifest failed")
        return {"error": str(exc), "content": None}


def write_case(run_id: str, tc_id: str, payload: dict) -> dict:
    """Checkpoint one case. The payload is redacted before it touches disk."""
    try:
        if not valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        if not valid_tc_id(tc_id):
            return {
                "error": (
                    "Refusing to checkpoint case id "
                    + repr(str(tc_id)[:40])
                    + "; it must look like TC-001."
                ),
                "content": None,
            }
        body = dict(payload or {})
        body.setdefault("tc_id", tc_id)
        body.setdefault("updated", time.time())
        _write_json(run_path(run_id) / CASES_DIR / (tc_id + ".json"), body)
        return {"error": None, "content": {"tc_id": tc_id}}
    except Exception as exc:
        logger.exception("mobile.run_store.write_case failed")
        return {"error": str(exc), "content": None}


def read_case(run_id: str, tc_id: str) -> dict:
    try:
        if not valid_run_id(run_id) or not valid_tc_id(tc_id):
            return {"error": "Invalid run or case id.", "content": None}
        return {
            "error": None,
            "content": _read_json(run_path(run_id) / CASES_DIR / (tc_id + ".json")),
        }
    except Exception as exc:
        logger.exception("mobile.run_store.read_case failed")
        return {"error": str(exc), "content": None}


def list_cases(run_id: str) -> dict:
    """Every checkpointed case body, ordered by tc_id."""
    try:
        if not valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        directory = run_path(run_id) / CASES_DIR
        bodies: list[dict] = []
        if directory.is_dir():
            for child in sorted(directory.glob("TC-*.json")):
                body = _read_json(child)
                if isinstance(body, dict):
                    bodies.append(body)
        return {"error": None, "content": bodies}
    except Exception as exc:
        logger.exception("mobile.run_store.list_cases failed")
        return {"error": str(exc), "content": None}


def write_screen(run_id: str, screen: object) -> dict:
    """Store one PRUNED screen under its own ``screen_id``.

    Idempotent by design: the id is stable across a one-pixel scroll, so the
    same screen re-observed rewrites one file rather than adding another. The
    payload goes through the same redacting writer as everything else here.

    A refusal is CONTENT for the caller to ignore: the writers are mid-run
    lifecycle functions, and a report that lost a wireframe is a far lesser
    failure than a case that lost its verdict.
    """
    try:
        if not valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        body = dict(screen) if isinstance(screen, dict) else {}
        screen_id = str(body.get("screen_id") or "")
        if not _RUN_ID_RE.match(screen_id):
            return {
                "error": (
                    "Refusing "
                    + repr(screen_id[:40])
                    + " as a screen id; a pruned screen carries a hex screen_id."
                ),
                "content": None,
            }
        _write_json(run_path(run_id) / SCREENS_DIR / (screen_id + ".json"), body)
        return {"error": None, "content": {"screen_id": screen_id}}
    except Exception as exc:
        logger.exception("mobile.run_store.write_screen failed")
        return {"error": str(exc), "content": None}


def list_screens(run_id: str) -> dict:
    """``{screen_id: screen}`` -- what the report joins a trace's ids against."""
    try:
        if not valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        directory = run_path(run_id) / SCREENS_DIR
        out: dict = {}
        if directory.is_dir():
            for child in sorted(directory.glob("*.json")):
                body = _read_json(child)
                if isinstance(body, dict) and body.get("screen_id"):
                    out[str(body["screen_id"])] = body
        return {"error": None, "content": out}
    except Exception as exc:
        logger.exception("mobile.run_store.list_screens failed")
        return {"error": str(exc), "content": None}


def _run_age(root: Path, manifest: dict, now: float) -> float:
    """Seconds since this run's NEWEST activity.

    Newest, not oldest: a run created three weeks ago whose last case was
    checkpointed yesterday is a run somebody is still using, and taking
    ``created`` alone would delete it.

    The lease heartbeat is deliberately NOT one of these timestamps. It is a
    separate keep-reason in :func:`gc_stale_runs`, because a live lease is
    always fresh -- folding it in here would make that branch unreachable, and
    an unreachable branch cannot be tested or mutated.
    """
    stamps = [float(manifest.get("created") or 0)]
    try:
        stamps.append(float(root.stat().st_mtime))
    except OSError:  # pragma: no cover - the dir was just listed
        pass
    try:
        for child in (root / CASES_DIR).glob("*.json"):
            stamps.append(float(child.stat().st_mtime))
    except OSError:  # pragma: no cover - defensive
        pass
    return float(now) - max(stamps)


def gc_stale_runs(*, now: float | None = None, keep_s: float = STALE_RUN_S) -> dict:
    """Delete run directories older than *keep_s*. Never raises.

    ``{"error", "content": {"removed": [...], "kept": [{run_id, reason}],
    "considered": n}}``.

    THE KILL-SWITCH IS READ HERE, at the innermost function that deletes. This
    removes a tester's own results -- an effect that outlives this process
    exactly like a download or a spawn -- which is why
    ``tests/mobile/test_mobile_killswitch_surface.py`` lists ``shutil.rmtree(``
    among its effect calls and why this function is not in its ``EXEMPT``.

    FOUR independent reasons keep a run, because a GC that eats a resumable run
    is worse than no GC at all:

    * ``no_manifest`` -- unidentifiable, so not ours to delete;
    * ``lease_live`` -- a chat is driving it right now, however old the files;
    * ``clock_skew`` -- the newest timestamp is in the FUTURE, so a clock change
      must not be read as age;
    * ``fresh`` -- inside the window. The threshold is strict: a run exactly
      *keep_s* old is kept.
    """
    try:
        if not settings.qa_mobile_run_enabled:
            return {
                "error": (
                    "Refusing to delete old runs: the mobile lane needs `"
                    + FLAG_NAME
                    + "=true` in `.env`. Nothing was removed."
                ),
                "content": None,
            }
        moment = _now(now)
        root = paths.sub("runs")
        removed: list[str] = []
        kept: list[dict] = []
        considered = 0
        if not root.is_dir():
            return {
                "error": None,
                "content": {"removed": removed, "kept": kept, "considered": 0},
            }
        for child in sorted(root.iterdir()):
            if not child.is_dir() or not valid_run_id(child.name):
                continue
            considered += 1
            manifest = _read_json(child / MANIFEST_FILE)
            if not isinstance(manifest, dict) or not manifest:
                kept.append({"run_id": child.name, "reason": "no_manifest"})
                continue
            lease = _read_json(child / LEASE_FILE)
            heartbeat = (
                float(lease.get("heartbeat") or 0) if isinstance(lease, dict) else 0.0
            )
            if heartbeat and (moment - heartbeat) <= LEASE_STALE_S:
                kept.append({"run_id": child.name, "reason": "lease_live"})
                continue
            age = _run_age(child, manifest, moment)
            if age < 0:
                kept.append({"run_id": child.name, "reason": "clock_skew"})
                continue
            if age <= float(keep_s):
                kept.append({"run_id": child.name, "reason": "fresh"})
                continue
            shutil.rmtree(child, ignore_errors=True)
            removed.append(child.name)
        if removed:
            logger.info("mobile.run_store: collected %d stale run(s)", len(removed))
        return {
            "error": None,
            "content": {
                "removed": removed,
                "kept": kept,
                "considered": considered,
            },
        }
    except Exception as exc:
        logger.exception("mobile.run_store.gc_stale_runs failed")
        return {"error": str(exc), "content": None}


def list_runs(limit: int = 50, *, gc: bool = True) -> dict:
    """Run summaries, newest first.

    Collects stale runs first and IGNORES the result: a collection that refused
    (the lane is off) or failed must never stop a tester listing their runs.
    ``gc=False`` is for a caller that wants a pure listing.
    """
    try:
        if gc:
            try:
                gc_stale_runs()
            except Exception:  # pragma: no cover - gc_stale_runs never raises
                logger.info("mobile.run_store: stale-run collection skipped")
        root = paths.sub("runs")
        out: list[dict] = []
        if root.is_dir():
            for child in root.iterdir():
                if not child.is_dir() or not valid_run_id(child.name):
                    continue
                manifest = _read_json(child / MANIFEST_FILE)
                manifest = manifest if isinstance(manifest, dict) else {}
                try:
                    modified = child.stat().st_mtime
                except OSError:
                    modified = 0.0
                out.append(
                    {
                        "run_id": child.name,
                        "created": manifest.get("created", 0),
                        "modified": modified,
                        "manifest": manifest,
                    }
                )
        out.sort(key=lambda item: float(item.get("modified") or 0), reverse=True)
        return {"error": None, "content": out[: max(1, int(limit))]}
    except Exception as exc:
        logger.exception("mobile.run_store.list_runs failed")
        return {"error": str(exc), "content": None}


#: A verdict that means the case is FINISHED -- it will not be handed out
#: again. Defined here, at the layer that decides what "done" means, and
#: imported by `report.DONE_VERDICTS`; `case_runner` accepts exactly these
#: plus the empty string it normalises away. One definition, because the last
#: verdict to be added reached two of the three copies and hung the run.
DONE_VERDICTS: tuple[str, ...] = ("pass", "fail", "blocked", "unverified")


def resume_point(run_id: str) -> dict:
    """``{done, failed, verdicts, next_index}`` for a resumed run."""
    try:
        listed = list_cases(run_id)
        if listed.get("error"):
            return listed
        done: list[str] = []
        failed: list[str] = []
        verdicts: dict[str, str] = {}
        for body in listed.get("content") or []:
            tc_id = str(body.get("tc_id") or "")
            verdict = str(body.get("verdict") or "")
            if not tc_id:
                continue
            verdicts[tc_id] = verdict
            # The SAME tuple report.py renders from and case_runner accepts.
            # It was three separate literals, and `unverified` reached two of
            # them: a case that proved nothing never counted as done, so the
            # scheduler re-served it forever and the run could not finish.
            if verdict in DONE_VERDICTS:
                done.append(tc_id)
            if verdict == "fail":
                failed.append(tc_id)
        manifest = (read_manifest(run_id) or {}).get("content") or {}
        order = [str(x) for x in (manifest.get("order") or [])]
        next_index = 0
        for index, tc_id in enumerate(order):
            if tc_id not in done:
                next_index = index
                break
        else:
            next_index = len(order)
        return {
            "error": None,
            "content": {
                "done": sorted(done),
                "failed": sorted(failed),
                "verdicts": verdicts,
                "next_index": next_index,
                "total": len(order),
            },
        }
    except Exception as exc:
        logger.exception("mobile.run_store.resume_point failed")
        return {"error": str(exc), "content": None}


# ---------------------------------------------------------------------------
# Lease
# ---------------------------------------------------------------------------


def _lease_path(run_id: str) -> Path:
    return run_path(run_id) / LEASE_FILE


def read_lease(run_id: str) -> dict:
    try:
        if not valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        return {"error": None, "content": _read_json(_lease_path(run_id))}
    except Exception as exc:
        logger.exception("mobile.run_store.read_lease failed")
        return {"error": str(exc), "content": None}


def acquire_lease(
    run_id: str,
    session_id: str,
    *,
    now: float | None = None,
    force: bool = False,
) -> dict:
    """Take or refresh the run's lease.

    ``{"error", "content": {"acquired", "holder", "taken_over_from", "reason"}}``.

    Take-over happens when the current holder's heartbeat is older than
    :data:`LEASE_STALE_S`, or when the caller passes ``force=True`` -- which is
    what an explicit "resume run X" from a second chat means. The displaced
    holder is recorded in ``taken_over_from``, and that is what makes its NEXT
    :func:`touch_lease` return ``taken_over`` instead of silently continuing to
    drive the same emulator.

    **This is read-decide-write and therefore not atomic as a whole.** The
    compare-after-swap at the end narrows the window: after writing, we re-read
    and, if another session already won, return ``acquired=False`` with
    ``reason="lost_race"`` rather than handing the caller a lease it does not
    hold. It does NOT close the window -- a loser whose re-read lands before the
    winner's write still sees itself and learns only at its next
    :func:`touch_lease`. Both behaviours are pinned by tests. The residual is
    bounded by the per-emulator lock in ``tools/mobile/locks.py``: two chats can
    briefly disagree about who owns a RUN RECORD, but not about who is driving
    the DEVICE. A file lock around the acquire itself was considered and
    rejected for this phase -- it trades a bounded, disclosed, tested race for
    an undisclosed stale-guard-file failure mode on a path no MCP tool can even
    reach yet, and Phase 3 is where the lock/lease pairing gets its owner.
    """
    try:
        if not valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        if not (isinstance(session_id, str) and _SESSION_RE.match(session_id)):
            return {
                "error": "Refusing " + repr(str(session_id)[:40]) + " as a session id.",
                "content": None,
            }
        moment = _now(now)
        current = _read_json(_lease_path(run_id))
        current = current if isinstance(current, dict) else None
        holder = str((current or {}).get("session_id") or "")
        heartbeat = float((current or {}).get("heartbeat") or 0)
        taken_from = ""
        reason = "new"
        if current and holder and holder != session_id:
            stale = (moment - heartbeat) > LEASE_STALE_S
            if not (stale or force):
                return {
                    "error": None,
                    "content": {
                        "acquired": False,
                        "holder": holder,
                        "taken_over_from": "",
                        "reason": "held_by_other",
                    },
                }
            taken_from = holder
            reason = "stale" if stale else "forced"
        elif current and holder == session_id:
            reason = "refresh"
            taken_from = str(current.get("taken_over_from") or "")
        payload = {
            "session_id": session_id,
            "heartbeat": moment,
            "acquired": moment
            if reason != "refresh"
            else current.get("acquired", moment),
            "taken_over_from": taken_from,
        }
        _write_json(_lease_path(run_id), payload)
        # Compare-after-swap. See the docstring: this narrows the read-decide-
        # write race rather than closing it, and saying so is the point -- a
        # caller told `acquired=True` for a lease somebody else now holds would
        # go on producing packets for a device it does not own.
        confirmed = _read_json(_lease_path(run_id))
        winner = str((confirmed or {}).get("session_id") or "")
        if winner and winner != session_id:
            return {
                "error": None,
                "content": {
                    "acquired": False,
                    "holder": winner,
                    "taken_over_from": "",
                    "reason": "lost_race",
                },
            }
        return {
            "error": None,
            "content": {
                "acquired": True,
                "holder": session_id,
                "taken_over_from": taken_from,
                "reason": reason,
            },
        }
    except Exception as exc:
        logger.exception("mobile.run_store.acquire_lease failed")
        return {"error": str(exc), "content": None}


def lease_status(run_id: str, session_id: str, *, now: float | None = None) -> dict:
    """``{state, holder, taken_over_from, age}`` from *session_id*'s point of view."""
    try:
        if not valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        current = _read_json(_lease_path(run_id))
        if not isinstance(current, dict) or not current.get("session_id"):
            return {
                "error": None,
                "content": {
                    "state": NONE,
                    "holder": "",
                    "taken_over_from": "",
                    "age": 0.0,
                },
            }
        holder = str(current.get("session_id") or "")
        age = _now(now) - float(current.get("heartbeat") or 0)
        state = HELD if holder == session_id else TAKEN_OVER
        return {
            "error": None,
            "content": {
                "state": state,
                "holder": holder,
                "taken_over_from": str(current.get("taken_over_from") or ""),
                "age": age,
            },
        }
    except Exception as exc:
        logger.exception("mobile.run_store.lease_status failed")
        return {"error": str(exc), "content": None}


def touch_lease(run_id: str, session_id: str, *, now: float | None = None) -> dict:
    """Refresh the heartbeat, or report that the lease was taken over.

    This is the call every packet-producing step makes first. A session that
    lost the lease is told ``state="taken_over"`` and the file is NOT written,
    so the new holder's heartbeat cannot be clobbered by the old one.
    """
    try:
        status = lease_status(run_id, session_id, now=now)
        if status.get("error"):
            return status
        content = dict(status["content"] or {})
        if content.get("state") == HELD:
            refreshed = acquire_lease(run_id, session_id, now=now)
            if refreshed.get("error"):
                return refreshed
            content["state"] = HELD
            content["taken_over_from"] = str(
                (refreshed["content"] or {}).get("taken_over_from") or ""
            )
            content["age"] = 0.0
        return {"error": None, "content": content}
    except Exception as exc:
        logger.exception("mobile.run_store.touch_lease failed")
        return {"error": str(exc), "content": None}


def takeover_message(run_id: str, holder: str) -> str:
    """The line the displaced chat is shown. No secret, no path, no run data."""
    return (
        "⚠️ This run was taken over by another chat, so this one has "
        "stopped driving the emulator. Run `" + str(run_id) + "` is now held by "
        "session `" + str(holder)[:40] + "`. Continue there, or start a new run "
        "here."
    )


# ---------------------------------------------------------------------------
# App-log evidence (plan mobile-app-evidence, P2)
# ---------------------------------------------------------------------------

EVIDENCE_DIR = "evidence"

#: A file name inside ``evidence/``: one path segment, bounded charset.
_EVIDENCE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")


def _write_text(target: Path, text: str) -> None:
    """The same tmp + ``os.replace`` + 0600 discipline as :func:`_write_json`.

    NO redaction here, and stated: :func:`redact` is dict-key based and cannot
    see free text. The caller (``tools/mobile_evidence/capture.py``) scrubs every
    line through the value, key and pair nets BEFORE calling this.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(str(text or ""))
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, target)


def _evidence_path(run_id: str, tc_id: object, name: str) -> Path | None:
    if not valid_run_id(run_id):
        return None
    if not (isinstance(name, str) and _EVIDENCE_NAME_RE.match(name)):
        return None
    root = run_path(run_id) / EVIDENCE_DIR
    if tc_id is None or tc_id == "":
        return root / name
    if not valid_tc_id(tc_id):
        return None
    return root / str(tc_id) / name


def write_evidence_text(run_id: str, tc_id: object, name: str, text: str) -> dict:
    """Write one scrubbed text file under ``evidence/`` (``tc_id`` None = run-level)."""
    try:
        target = _evidence_path(run_id, tc_id, name)
        if target is None:
            return {
                "error": "Refusing evidence path "
                + repr((str(run_id)[:40], str(tc_id)[:20], str(name)[:60])),
                "content": None,
            }
        _write_text(target, text)
        return {"error": None, "content": {"path": str(target)}}
    except Exception as exc:
        logger.exception("mobile.run_store.write_evidence_text failed")
        return {"error": str(exc), "content": None}


def write_evidence_json(run_id: str, tc_id: object, name: str, payload: object) -> dict:
    """Write one JSON document under ``evidence/`` through the redacting writer."""
    try:
        target = _evidence_path(run_id, tc_id, name)
        if target is None:
            return {
                "error": "Refusing evidence path "
                + repr((str(run_id)[:40], str(tc_id)[:20], str(name)[:60])),
                "content": None,
            }
        _write_json(target, payload)
        return {"error": None, "content": {"path": str(target)}}
    except Exception as exc:
        logger.exception("mobile.run_store.write_evidence_json failed")
        return {"error": str(exc), "content": None}


def list_evidence(run_id: str) -> dict:
    """``{tc_id or "": [names]}`` for everything under ``evidence/``."""
    try:
        if not valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        root = run_path(run_id) / EVIDENCE_DIR
        out: dict = {}
        if root.is_dir():
            for child in sorted(root.iterdir()):
                if child.is_file() and _EVIDENCE_NAME_RE.match(child.name):
                    out.setdefault("", []).append(child.name)
                elif child.is_dir() and valid_tc_id(child.name):
                    out[child.name] = sorted(
                        grandchild.name
                        for grandchild in child.iterdir()
                        if grandchild.is_file()
                        and _EVIDENCE_NAME_RE.match(grandchild.name)
                    )
        return {"error": None, "content": out}
    except Exception as exc:
        logger.exception("mobile.run_store.list_evidence failed")
        return {"error": str(exc), "content": None}


def read_evidence_text(run_id: str, tc_id: object, name: str) -> dict:
    """One evidence file as text, or ``content: None`` when it does not exist."""
    try:
        target = _evidence_path(run_id, tc_id, name)
        if target is None:
            return {"error": "Invalid evidence path.", "content": None}
        if not target.is_file():
            return {"error": None, "content": None}
        return {
            "error": None,
            "content": target.read_text(encoding="utf-8", errors="replace"),
        }
    except Exception as exc:
        logger.exception("mobile.run_store.read_evidence_text failed")
        return {"error": str(exc), "content": None}
