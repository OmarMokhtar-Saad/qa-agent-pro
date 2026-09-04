"""One emulator, one run at a time -- and the KERNEL decides who is alive.

Deliberately NOT a threading or asyncio lock. The thing being serialised is a
physical emulator shared by every chat on the machine, and the contenders are
separate OS processes (the MCP server, a second editor, the detached
provisioner). A lock that only existed inside one interpreter would not see
them.

THE INVARIANT, and the reason this module was rewritten on 2026-09-04:

    A lock is released only by its HOLDER, or by the KERNEL.

Nothing else may release it -- not a contender, not a reaper, not a
force-release, not an ``unlink``. What this replaced asked the lock FILE whether
its holder was still alive, via a pid probe and a body timestamp, and then stole
the lock when it guessed "no". Every variant of that produced two simultaneous
holders: the forked race harness reproduced 25 overlapping holders in 40 trials
against the old module.

An advisory ``flock`` is dropped by the OS when the holding process exits,
however it exits -- cleanly, killed, or crashed. So there is no such thing as a
stale lock here, nothing to detect, nothing to steal, and no check-then-act
window. The whole staleness apparatus is DELETED rather than corrected.

WHY THERE IS NO ``unlink``. ``flock`` is bound to an INODE, not to a path.
Removing the file does not revoke a live holder's lock on its still-open fd; it
only lets the "force-releaser" create a NEW file at the same path and lock a
DIFFERENT inode, at which point two processes hold independent exclusive locks
and both drive the device -- deterministically, not as a race. Measured on this
machine before the rewrite. The lock file is therefore IMMORTAL: it is created
if absent and never removed, it carries no meaning, and an empty one costs
nothing.

WHAT THE BODY IS FOR. ``{pid, owner, time}`` is stamped in AFTER the lock is
held, for a human reading the file or a refusal message. **No code path reads it
back to make a decision.** That is the sharpest single difference from what this
replaced.

OWNERSHIP LIVES IN MEMORY, in :data:`_HELD`, guarded by :data:`_MUTEX`. The
mutex is not decoration: the heartbeat writer is a real background thread
(``tools/mobile/heartbeat.py``) that releases a run's lock when it loses the
run's lease, while the event-loop thread may be inside :func:`acquire`.
``heartbeat.py`` guards its own ``_WRITERS`` dict the same way, for the same
reason.

Never raises: every public function returns ``{"error", "content"}``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from tools.mobile import paths

logger = logging.getLogger(__name__)


class LockUnsupported(RuntimeError):
    """Neither locking primitive exists on this platform."""


#: The lock the whole mobile lane takes. ONE lock for the lane, not one per
#: serial: ``_mobile_device_stage`` is what picks, boots and provisions the
#: device, so the serial does not exist until after the most contended step has
#: already run, and a per-serial lock could not cover the step it most needs to.
EMULATOR_LOCK = "emulator"

#: The lock the DETACHED provisioner holds for its own lifetime. The worker
#: holds it, never the chat that started the worker -- so the kernel releases it
#: when that worker exits or crashes and there is nothing to reap.
PROVISION_LOCK = "provision"


def new_provisioning_owner() -> str:
    """A fresh owner label for the pre-run phase, when no ``run_id`` exists yet.

    A FUNCTION, not a constant, and that is the whole point.

    AN OWNER LABEL MUST IDENTIFY EXACTLY ONE HOLDER. This used to be the
    module-level constant ``"provisioning:" + str(os.getpid())`` -- one label
    per PROCESS -- and :func:`acquire` grants a matching label reentrantly. A
    label two callers can both present is not an owner, so reentrancy on it was
    not reentrancy: it was SHARING. Two concurrent new-run calls in one MCP
    server both got ``acquired=True`` and both drove the same emulator, one
    caller's release freed the other's hold, and the loser's relabel then failed
    -- three defects, one shared label. Found by an executing review, on the
    primary path, with no stub.

    So each call mints its own. The label still names the process, because that
    is what a refusal message needs to say, but the random half is what makes it
    an identity.
    """
    return "provisioning:%d:%s" % (os.getpid(), secrets.token_hex(4))


#: A hold longer than this is DISCLOSED -- logged CRITICAL, surfaced by
#: qa-doctor -- and never broken. A hung holder is a human problem; breaking its
#: lock is the two-holder defect this module exists to delete. A module constant
#: rather than a setting: it tunes a log line, not behaviour.
HELD_TOO_LONG_S = 600

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: The earliest wall-clock a lock body could plausibly have been stamped at
#: (2020-09-13). A RANGE, deliberately, and not a fourth list of bad shapes.
#:
#: Three review rounds closed this by enumerating: first `""`, then unparseable
#: bodies, then `true` / huge ints / `inf` / `NaN` / future stamps. Each list was
#: complete for the values somebody had thought of, and each time a value nobody
#: had thought of walked through -- most recently `{"time": 1e-300}`, which is a
#: positive float, passes `> 0`, and produces an age of 1.8e9 seconds: a
#: CRITICAL log about a wedged holder and a qa-doctor line telling a tester to
#: quit the editor. A timestamp is only a timestamp inside a plausible window,
#: and everything outside it means "the body does not say", which is what
#: :func:`_body_age` already promises.
_PLAUSIBLE_FLOOR_S = 1_600_000_000.0

#: Guards every read and mutation of :data:`_HELD`, across the WHOLE body of
#: each public function -- the window that matters is between the ``flock``
#: succeeding and ``_HELD`` recording it, not the dict write alone. ``RLock`` so
#: that one of these functions calling another cannot self-deadlock.
_MUTEX = threading.RLock()


@dataclass
class _Held:
    """One lock this process holds. The fd IS the ownership.

    ``lease`` is the run lease the lock was taken UNDER, and it exists for one
    reason: an owner label must identify exactly one holder, and a ``run_id``
    does not. Every chat that ever claims a run presents the same run id, so a
    leftover heartbeat writer for that run -- one whose lease was revoked chats
    ago -- would otherwise release the lock the CURRENT lease holder is using,
    handing the device to a third process mid-run. Measured before this field
    existed.

    It is deliberately NOT the owner label and is never rendered: the
    tester-facing owner stays the run id, because that is what a refusal has to
    be able to name and what a takeover instruction has to be able to use.
    """

    fd: int
    owner: str
    since: float
    lease: str = ""


_HELD: dict[str, _Held] = {}


def _refuse_name(name: object) -> dict | None:
    """The refusal dict for a bad lock name, or ``None`` when it is fine.

    ENFORCED AT EACH PUBLIC BOUNDARY, not only inside :func:`lock_path`. Putting
    the check solely in `lock_path` and trusting it to propagate made it INERT
    for :func:`holder`: `_read` catches ``(OSError, ValueError)`` and answers
    ``None``, `_probe_free` catches ``Exception`` and answers "busy", so
    ``holder("../escape")`` came back ``{"held": True}`` with no error at all.
    Two conservative handlers, one guard that never fired.
    """
    if not _NAME_RE.match(str(name or "")):
        return {
            "error": "Refusing " + repr(str(name)[:40]) + " as a lock name.",
            "content": None,
        }
    return None


def lock_path(name: str) -> Path:
    """The file a lock name maps to. Raises on a name that is not one.

    THE GATE LIVES HERE, at the single place a name becomes a path, rather than
    in ``acquire`` alone. ``holder`` used to skip it, and ``holder`` calls
    ``_probe_free``, which CREATES the file: an executing review turned that into
    0600 files at ``locks/../escape.lock`` and at an absolute path outside the
    cache entirely, and scattered nineteen junk files through a real
    ``~/.qa-agents/mobile/locks``. One check at the choke point cannot be
    skipped by a new call site.

    A ``ValueError`` rather than a dict, because every caller is already inside
    a ``try`` that turns it into ``{"error", "content"}`` -- the never-raise
    contract is kept at the public boundary, which is where it is promised.
    """
    if not _NAME_RE.match(str(name or "")):
        raise ValueError("Refusing " + repr(str(name)[:40]) + " as a lock name.")
    return paths.sub("locks") / (str(name) + ".lock")


def _read(name: str) -> dict | None:
    """The lock body, for MESSAGES AND LOGS ONLY. Never a decision input."""
    try:
        target = lock_path(name)
        if not target.is_file():
            return None
        body = json.loads(target.read_text(encoding="utf-8"))
        return body if isinstance(body, dict) else None
    except (OSError, ValueError):
        return None


def _body_age(body: object, moment: float) -> float:
    """How long ago the body says the lock was stamped. 0.0 when it does not say.

    ONE place, because there were two and only one of them was fixed. A body
    with no usable ``time`` used to make this arithmetic return ~1.8e9, so
    ``held_too_long`` fired on a lock taken a moment ago -- and the second site,
    inside :func:`acquire`, logged CRITICAL about a wedged holder called
    ``pid None``. A disclosure that cries wolf is worse than none, because the
    design's honest bound is that a hung holder is DISCLOSED rather than broken.
    """
    raw = None
    try:
        raw = (body or {}).get("time")
    except (AttributeError, TypeError):
        return 0.0
    # `True` is an int in Python, and `float(True) == 1.0` sails past a `> 0`
    # gate -- so a body of `{"time": true}` produced an age of 1.8e9 and a
    # CRITICAL about a wedged holder, which is the exact false alarm this
    # helper was extracted to stop. A bool is not a timestamp.
    if isinstance(raw, bool) or raw is None:
        return 0.0
    try:
        stamped = float(raw)
    except (AttributeError, TypeError, ValueError, OverflowError):
        # OverflowError is NOT a subclass of the other three: a legal JSON body
        # with a huge integer `time` raised straight out of here, and the lane
        # then refused every acquire without saying the lock file was corrupt.
        return 0.0
    if stamped != stamped or stamped in (float("inf"), float("-inf")):
        return 0.0  # NaN and the infinities are not timestamps either
    if not _PLAUSIBLE_FLOOR_S < stamped <= moment:
        # A future stamp is as meaningless as a missing one, and reporting a
        # negative age would put `held_too_long` on the wrong side of nothing.
        return 0.0
    return moment - stamped


def _stamp(name: str, fd: int, owner: str, moment: float) -> None:
    """Write the diagnostic body, AFTER the lock is held. Best effort.

    Written through the locked fd rather than a temp file plus ``os.replace``:
    the old module's ``_write`` raced itself on the ``.tmp`` path and raised
    ``FileNotFoundError`` out of ``os.replace``, which the caller then saw as an
    error instead of as contention. There is nothing to race here -- we hold the
    lock -- and a body that fails to write costs a log line, not a run.
    """
    try:
        payload = json.dumps(
            {"pid": os.getpid(), "owner": str(owner)[:120], "time": float(moment)},
            sort_keys=True,
        )
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, payload.encode("utf-8"))
        os.truncate(fd, len(payload))
    except OSError:
        logger.info("mobile.locks: could not stamp the body of %s", name)


def _lock_fd(fd: int) -> None:
    """Take an exclusive, NON-BLOCKING lock on *fd*, or raise.

    Non-blocking is not a preference: a mobile tool call that waited on the
    emulator would die at the client's own timeout and tell the tester nothing,
    where a refusal names the holder and offers a way forward.

    WINDOWS TAKES A DIFFERENT STEP ORDER. ``msvcrt.locking`` locks a BYTE REGION
    from the current file position and needs that region to exist, so locking
    byte 0 of a freshly created empty file is not the POSIX sequence with a
    different call name -- the caller writes the body first there. **No Windows
    machine has run this code**, consistent with the mobile lane's standing
    disclosure in CLAUDE.md and docs/MOBILE_TESTING.md.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows
        pass
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    try:
        import msvcrt
    except ImportError as exc:  # pragma: no cover - neither primitive exists
        raise LockUnsupported("neither fcntl nor msvcrt is available") from exc
    # pragma: no cover - no Windows machine has run this
    #
    # The pad byte is written ONLY when the file is empty. `msvcrt.locking`
    # needs the byte region to exist, but writing unconditionally overwrites the
    # first byte of an INCUMBENT holder's JSON body on a failed acquire -- and
    # then `_read` cannot parse it and the refusal loses the holder's name,
    # which is the one thing the message exists to say.
    if os.fstat(fd).st_size == 0:
        os.write(fd, b" ")
    os.lseek(fd, 0, os.SEEK_SET)
    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)


def _unlock_fd(fd: int) -> None:
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows
        pass
    else:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            # The fd is closed right after this, which releases it anyway.
            logger.debug("mobile.locks: unlock call failed for fd %s", fd)
        return
    try:  # pragma: no cover - Windows only
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except (ImportError, OSError):  # pragma: no cover - Windows only
        logger.debug("mobile.locks: unlock call failed for fd %s", fd)


def _probe_free(name: str) -> bool:
    """True when NO process holds *name* right now. Diagnostics only.

    Takes the lock and drops it again, so the only lock it ever releases is its
    own -- the invariant is not bent. ``False`` on any error, because "unknown"
    must read as "busy": a diagnostic that guesses "free" would invite a caller
    to act on a device somebody else is driving.

    ITS COST, stated honestly rather than waved away. This holds the exclusive
    lock for the width of one ``flock`` pair, and for that instant a REAL
    contender is refused. That used to be excused with "nothing but diagnostics
    calls this function", which stopped being true when ``qa-doctor`` -- the
    first tool a tester runs on a new machine -- became a caller. Measured at
    41,055 probes against 22,571 real acquires in one 4-second race: 78 spurious
    refusals, 0.35%. qa-doctor probes ONCE per run, so a tester meets this at
    one flock pair wide, and a refusal is a message rather than a lost run --
    but the number is here so nobody has to guess again.
    """
    fd = -1
    try:
        target = lock_path(name)  # raises on a name that is not one
        if not target.parent.is_dir():
            return True
        fd = os.open(target, os.O_CREAT | os.O_RDWR, 0o600)
        _lock_fd(fd)
        _unlock_fd(fd)
        return True
    except Exception:
        return False
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass


def holder(name: str = EMULATOR_LOCK, *, now: float | None = None) -> dict:
    """``{held, pid, owner, age, held_too_long, mine}`` without taking anything.

    Diagnostics and qa-doctor only. ``stale`` is deliberately GONE: it was the
    age-from-body defect wearing a different hat, and leaving it would let that
    defect come back through a new call path. ``held_too_long`` is a DISCLOSURE,
    never a decision.
    """
    try:
        refusal = _refuse_name(name)
        if refusal is not None:
            return refusal
        moment = float(now) if now is not None else time.time()
        with _MUTEX:
            mine = _HELD.get(str(name))
            if mine is not None:
                age = moment - mine.since
                return {
                    "error": None,
                    "content": {
                        "held": True,
                        "pid": os.getpid(),
                        "owner": mine.owner,
                        "age": age,
                        "held_too_long": age > HELD_TOO_LONG_S,
                        "mine": True,
                    },
                }
        # THE FILE IS IMMORTAL, so its existence proves nothing and its body
        # proves only that somebody once stamped it. Whether the lock is held
        # RIGHT NOW is a question only the kernel can answer, so ask it: a
        # non-blocking probe that immediately releases whatever it took. The
        # probe releases only its OWN lock, never anybody else's, so the
        # invariant holds. Its cost is a microseconds-wide window in which a
        # real contender could be refused -- which is why nothing but
        # diagnostics calls this function.
        body = _read(name) or {}
        free = _probe_free(name)
        if free:
            return {
                "error": None,
                "content": {
                    "held": False,
                    "pid": 0,
                    "owner": "",
                    "age": 0.0,
                    "held_too_long": False,
                    "mine": False,
                },
            }
        age = _body_age(body, moment)
        return {
            "error": None,
            "content": {
                "held": True,
                "pid": int(body.get("pid") or 0),
                "owner": str(body.get("owner") or ""),
                "age": age,
                "held_too_long": age > HELD_TOO_LONG_S,
                "mine": False,
            },
        }
    except Exception as exc:
        logger.exception("mobile.locks.holder failed")
        return {"error": str(exc), "content": None}


def acquire(
    name: str = EMULATOR_LOCK,
    *,
    owner: str = "",
    lease: str = "",
    now: float | None = None,
) -> dict:
    """Take the lock, or refuse naming who holds it.

    ``{"error", "content": {"acquired", "owner", "holder", "reentrant",
    "same_process", "reason"}}``. The refusal is a CONTENT value rather than an
    error: "somebody else is using the emulator" is a normal state a handler
    renders, not a fault.
    """
    try:
        refusal = _refuse_name(name)
        if refusal is not None:
            return refusal
        label = str(owner or "").strip()
        if not label:
            return {
                "error": (
                    "A lock needs an owner: an unlabelled holder cannot be "
                    "named in a refusal and cannot be matched on release."
                ),
                "content": None,
            }
        key = str(name)
        moment = float(now) if now is not None else time.time()
        with _MUTEX:
            mine = _HELD.get(key)
            if mine is not None:
                if mine.owner == label:
                    # ADOPT the caller's lease. A same-owner acquire means this
                    # caller has just proved, through `session.claim`, that it
                    # holds the run's lease -- so it becomes the authority for
                    # releasing this lock, and the previous chat's writer stops
                    # being able to. That is the whole point of recording it.
                    if lease:
                        mine.lease = str(lease)
                    # Reentrant: no syscall, no write. This is what makes the
                    # submit path free once the packet path has taken the lock.
                    return {
                        "error": None,
                        "content": {
                            "acquired": True,
                            "owner": label,
                            "holder": label,
                            "reentrant": True,
                            "same_process": True,
                            "reason": "already_held",
                        },
                    }
                return {
                    "error": None,
                    "content": {
                        "acquired": False,
                        "owner": label,
                        "holder": mine.owner,
                        "reentrant": False,
                        "same_process": True,
                        "reason": "held_in_this_process",
                    },
                }
            paths.ensure_tree()
            target = lock_path(key)
            target.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(target, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                _lock_fd(fd)
            except LockUnsupported:
                os.close(fd)
                logger.warning(
                    "mobile.locks: no file-locking facility on %s; refusing the "
                    "lock rather than admitting two holders",
                    os.name,
                )
                return {
                    "error": None,
                    "content": {
                        "acquired": False,
                        "owner": label,
                        "holder": "",
                        "reentrant": False,
                        "same_process": False,
                        "reason": "no_lock_facility",
                    },
                }
            except (BlockingIOError, OSError):
                os.close(fd)
                body = _read(key) or {}
                age = _body_age(body, moment)
                if age > HELD_TOO_LONG_S:
                    logger.critical(
                        "mobile.locks: %s has been held by pid %s for %.0fs. "
                        "Nothing here will break it -- a lock broken under a "
                        "live holder is two holders. If that process is wedged, "
                        "a human has to look.",
                        key,
                        body.get("pid"),
                        age,
                    )
                return {
                    "error": None,
                    "content": {
                        "acquired": False,
                        "owner": label,
                        "holder": str(body.get("owner") or ""),
                        "reentrant": False,
                        "same_process": False,
                        "reason": "held_by_another_process",
                    },
                }
            _HELD[key] = _Held(fd=fd, owner=label, since=moment, lease=str(lease or ""))
            _stamp(key, fd, label, moment)
            return {
                "error": None,
                "content": {
                    "acquired": True,
                    "owner": label,
                    "holder": label,
                    "reentrant": False,
                    "same_process": False,
                    "reason": "acquired",
                },
            }
    except Exception as exc:
        logger.exception("mobile.locks.acquire failed")
        return {"error": str(exc), "content": None}


def release(
    name: str = EMULATOR_LOCK,
    *,
    owner: str = "",
    lease: str = "",
    as_holder: bool = False,
    force: bool = False,
) -> dict:
    """Release a lock THIS PROCESS holds. ``{released, reason}``.

    ``force`` bypasses only the owner LABEL, and only for a lock this process
    already holds. It can never touch another process's lock, because there is
    no code here that could: the fd is the ownership, and we do not have theirs.

    ``as_holder`` is a HANDLER saying "I have just claimed this run's lease, so
    I am its authority" -- and it has to be said, not inferred. It is opt-in at
    every seam above this one for that reason: while `session.release_device_lock`
    defaulted it to True, the DISPLACED branches -- which have just been told
    they lost the lease -- asserted it by saying nothing at all. The lease check
    used to skip itself for an empty ``lease``, which made the ABSENCE of a
    credential act as a credential: a writer that had lost its token was
    indistinguishable from a handler that had just claimed the run, and only
    ``heartbeat.start``'s refusal of an empty token kept it unreachable. That is
    the "unreachable by construction" argument this module's docstring already
    records failing twice.
    """
    try:
        refusal = _refuse_name(name)
        if refusal is not None:
            return refusal
        key = str(name)
        with _MUTEX:
            mine = _HELD.get(key)
            if mine is None:
                return {
                    "error": None,
                    "content": {"released": False, "reason": "not held"},
                }
            if not force and mine.owner != str(owner or "").strip():
                return {
                    "error": None,
                    "content": {
                        "released": False,
                        "reason": "held by " + mine.owner,
                    },
                }
            if not force and not as_holder and str(lease or "") != mine.lease:
                # RIGHT RUN, WRONG LEASE. A heartbeat writer from a chat that
                # was displaced beats on until it notices, and when it does it
                # must not give away the device the CURRENT holder of the same
                # run is driving. A caller that presents no lease is a handler
                # that has just claimed the run, and is trusted.
                #
                # NOTE THE MISSING `and mine.lease`. With it, a lock recorded
                # with no lease was releasable by ANY writer for that run id --
                # unreachable today only because every takeover route happens to
                # adopt a lease, which is exactly the "unreachable by
                # construction" argument that already failed twice here (the
                # reaper, and the per-process placeholder). Without it, a writer
                # can only release a lock whose lease authority was actually
                # recorded, so recording it is load-bearing rather than
                # decorative.
                return {
                    "error": None,
                    "content": {
                        "released": False,
                        # The reason distinguishes the two cases, because a log
                        # line a future debugger reads should not say "a newer
                        # lease" when in fact NO lease was ever recorded.
                        "reason": (
                            "held under a different lease for " + mine.owner
                            if mine.lease
                            else "held with no recorded lease authority for "
                            + mine.owner
                        ),
                    },
                }
            try:
                _unlock_fd(mine.fd)
                os.close(mine.fd)
            except OSError:
                # An fd already closed underneath us is still released. Popping
                # it is the never-raise contract AND the fd-leak guard: the
                # entry must go on EVERY path, including this one.
                logger.info("mobile.locks: fd for %s was already gone", key)
            finally:
                _HELD.pop(key, None)
            # NO unlink. The file is immortal -- see the module docstring.
            return {"error": None, "content": {"released": True, "reason": ""}}
    except Exception as exc:
        logger.exception("mobile.locks.release failed")
        return {"error": str(exc), "content": None}


def relabel(name: str = EMULATOR_LOCK, *, from_owner: str, to_owner: str) -> dict:
    """Rename the in-memory owner of a lock this process holds. ``{relabelled}``.

    The SAME fd keeps the SAME kernel lock: this is not release-then-reacquire,
    which would open a window in which another process could take the device
    between the two. It exists for one real sequence -- the new-run path must
    hold the device BEFORE a ``run_id`` exists, so it acquires under
    a label from :func:`new_provisioning_owner` and relabels once the run is
    planned.
    """
    try:
        refusal = _refuse_name(name)
        if refusal is not None:
            return refusal
        key = str(name)
        old = str(from_owner or "").strip()
        new = str(to_owner or "").strip()
        if not new:
            return {
                "error": "A lock needs an owner; refusing to relabel to nothing.",
                "content": None,
            }
        with _MUTEX:
            mine = _HELD.get(key)
            if mine is None:
                return {
                    "error": None,
                    "content": {
                        "relabelled": False,
                        "reason": "not held",
                        "holder": "",
                    },
                }
            if mine.owner != old:
                # `holder` is the real current OWNER LABEL, not prose. A caller
                # rendering this refusal needs a label it can put in a
                # `run_id=` instruction; handing it the reason string produced
                # `run_id="held by mrun-..."`, which cannot work.
                return {
                    "error": None,
                    "content": {
                        "relabelled": False,
                        "reason": "held by " + mine.owner,
                        "holder": mine.owner,
                    },
                }
            mine.owner = new
            _stamp(key, mine.fd, new, mine.since)
            return {"error": None, "content": {"relabelled": True, "reason": ""}}
    except Exception as exc:
        logger.exception("mobile.locks.relabel failed")
        return {"error": str(exc), "content": None}


def held_names() -> list[str]:
    """Lock names this process holds, sorted. Pure introspection, for tests."""
    with _MUTEX:
        return sorted(_HELD)
