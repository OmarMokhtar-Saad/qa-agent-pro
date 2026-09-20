"""A run-free passive network watch, for a GUI client.

The mobile lane ALREADY captures what a device puts on the wire, root-free:
``tools/mobile_evidence/capture.begin_network`` / ``finish_network`` ask the
EMULATOR CONSOLE (through ``adb ... emu``, which carries the console auth token
itself, so this server never reads or stores one) to record a pcap, sample
``/proc/net/tcp`` for socket owners alongside it, parse the result into host /
port / protocol / bytes / first-seen rows, and DELETE the pcap. That is the one
producer of this fact, and it is documented in ``docs/MOBILE_TESTING.md`` under
*The network capture*.

The only thing it lacks for a desktop client is an entry point: those functions
are per-CASE, keyed by ``run_id`` and ``tc_id`` inside a mobile run. This module
is that entry point and nothing else -- it invents no parsing, no sampling and
no capture mechanism of its own. Reimplementing any of it in the desktop app
would be a second producer of the same fact, and the two would disagree the
first time either changed.

**The rules the lane already has are the rules here.**

* Registration follows ``mcp_handlers._mobile_lane_enabled()`` (no flag), and
  ``capture._refusal()`` is called for every action, so a flag-off install
  refuses BY NAME without an adb call.
* ``apply=true`` is required to START, because starting touches the device.
  STOP never requires it: stop REVERTS, and a teardown a tester cannot run
  because they forgot an argument is how a capture gets left running.
* Emulator only. A physical device is refused by name by ``adb.emu`` itself --
  the console does not exist there -- and that refusal is passed through rather
  than reworded.
* The pcap is deleted by ``finish_network``; this module keeps only the parsed
  summary, and it keeps it OUTSIDE the run store.

**Where the artifact lives, and for how long.** ``finish_network`` writes its
summary through ``run_store.write_evidence_json``, under the run id it is given.
A watch's id is fabricated, so that write would file desktop observations inside
the real run store under a run nobody can find from any report -- a durable
directory the tester never asked for. So ``stop`` MOVES the summary: it writes it
to ``<mobile cache>/watch/<serial>/watch-<stamp>.json`` and REMOVES the
fabricated run directory, guarded so nothing whose id lacks
:data:`WATCH_RUN_PREFIX` can ever be the thing removed. The watch file is the
desktop app's to read and export; nothing in the lane looks for it.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)

#: Watches live in this process only. A watch is a live console capture plus a
#: sampler thread, and both die with the server -- persisting a handle would
#: promise a caller it could resume something that is already gone.
_WATCHES: dict = {}

#: The pseudo-run identity a desktop watch borrows. It is NOT a run id from the
#: mobile lane: a watch has no case, no script and no report, and reusing a real
#: run's id would file desktop observations under somebody's test run.
WATCH_RUN_PREFIX = "desktop-watch"

#: How long one watch may run before a `status` call reports it as EXPIRED and
#: refuses to keep claiming it is live. A watch holds an emulator console
#: capture and a once-a-second sampler; a GUI that crashes with one open would
#: otherwise leave both running with nothing to stop them. The number is a
#: freshness bound on a CLAIM, not a cap on work.
MAX_WATCH_S = 3600

ACTIONS = ("start", "stop", "status")


def _now() -> float:
    return time.time()


#: What a device serial may look like: adb serials (`emulator-5554`,
#: `R5CT30ABCDE`, `192.168.1.20:5555`) and nothing that can name a path. A
#: serial reaches a directory name AND an rmtree in this module, so the guard
#: is the SHAPE of the token, not a prefix on the id built from it: the prefix
#: check downstream still passes `desktop-watch-../../x`.
_SERIAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


def _key(serial: str) -> str:
    """The serial as a safe token, or "" when it is not one.

    Empty means "refuse": every caller already treats an empty key as a
    missing serial, so a traversal-shaped serial is refused by the same path,
    and the refusal text names the shape that was expected.
    """
    text = str(serial or "").strip()
    if not text or not _SERIAL_RE.match(text) or text.strip(".") == "":
        return ""
    return text


def _serial_refusal(serial: str) -> str:
    if not str(serial or "").strip():
        return "a device serial is required"
    return (
        "Refusing serial %r: a device serial is letters, digits and . _ : - only "
        "(as `adb devices` prints it). It names a directory here, so anything "
        "path-shaped is refused by name." % str(serial)[:80]
    )


def _under(child: Path, parent: Path) -> bool:
    """True when ``child`` resolves inside ``parent``. The second lock on the door."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def _watch_id(serial: str) -> str:
    return WATCH_RUN_PREFIX + "-" + (_key(serial) or "unknown")


async def start(serial: str, package: str = "", apply: bool = False) -> dict:
    """Begin a watch on ``serial``. Needs ``apply=true``: it touches the device."""
    from tools.mobile_evidence import capture

    refusal = capture._refusal()
    if refusal:
        return {"error": refusal, "content": None}
    key = _key(serial)
    if not key:
        return {"error": _serial_refusal(serial), "content": None}
    if not apply:
        return {
            "error": (
                "Refusing to start a network watch without `apply=true`. Starting "
                "asks the emulator console to record and samples the device's "
                "socket table; stopping needs no acknowledgement, because a "
                "teardown a tester cannot run is how a capture gets left running."
            ),
            "content": None,
        }
    if key in _WATCHES:
        # `already` is a FIELD, not a wording difference: a double-clicked
        # Start must not read to a caller as a fresh capture beginning now,
        # because the rows it eventually gets will reach back to the first one.
        return {
            "error": None,
            "content": {
                "serial": key,
                "state": "watching",
                "already": True,
                "started_at": _WATCHES[key]["started_at"],
            },
        }
    run_id = _watch_id(key)
    begun = await capture.begin_network(key, str(package or ""), run_id, "watch", 0)
    if begun.get("error"):
        return begun
    record = begun.get("content") or {}
    if not record.get("started"):
        # A skip is an ANSWER -- "this is a phone", "the console said no" --
        # and it is returned with its own stage rather than reworded here.
        return {"error": None, "content": dict(record, serial=key, state="not started")}
    _WATCHES[key] = {
        "run_id": run_id,
        "package": str(package or ""),
        "begun": record,
        "started_at": _now(),
    }
    return {
        "error": None,
        "content": {
            "serial": key,
            "state": "watching",
            "already": False,
            "started_at": _WATCHES[key]["started_at"],
        },
    }


def watch_dir(serial: str) -> Path:
    """Where a watch's summary lands: beside the lane's cache, not inside a run."""
    from tools.mobile import paths

    return paths.sub("watch") / (_key(serial) or "unknown")


def _relocate(run_id: str, serial: str, record: dict) -> str:
    """Move the summary out of the run store, and remove the fabricated run.

    Returns the path written, or "". Never raises: a watch that cannot tidy up
    is still a watch that must return its rows.
    """
    written = ""
    try:
        from tools.mobile import paths as _paths

        target_dir = watch_dir(serial)
        if not _under(target_dir, _paths.sub("watch")):
            raise ValueError("watch dir escaped the watch root: %s" % target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / ("watch-%d.json" % int(time.time()))
        target.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        written = str(target)
    except Exception as exc:
        logger.info("network_watch: could not write the watch summary: %s", exc)
    try:
        from tools.mobile import paths

        # The guard is the load-bearing line: only a directory whose id this
        # module fabricated can be removed here. A run id that came from
        # anywhere else must never reach an rmtree.
        tail = str(run_id)[len(WATCH_RUN_PREFIX) + 1 :]
        if str(run_id).startswith(WATCH_RUN_PREFIX + "-") and _key(tail):
            run_path = paths.run_dir(str(run_id))
            # Prefix AND shape AND containment: the id was fabricated here, its
            # serial part is a plain token, and the directory is inside the run
            # store. The prefix alone was shown insufficient by execution.
            if run_path.is_dir() and _under(run_path, paths.sub("runs")):
                shutil.rmtree(run_path, ignore_errors=True)
    except Exception as exc:
        logger.info("network_watch: could not remove the fabricated run dir: %s", exc)
    return written


async def stop(serial: str) -> dict:
    """End a watch and return its parsed rows. Never needs ``apply``."""
    from tools.mobile_evidence import capture

    refusal = capture._refusal()
    if refusal:
        return {"error": refusal, "content": None}
    key = _key(serial)
    live = _WATCHES.pop(key, None)
    if live is None:
        return {
            "error": None,
            "content": {"serial": key, "state": "not watching", "rows": []},
        }
    finished = await capture.finish_network(
        key, live["package"], live["run_id"], "watch", 0, live["begun"]
    )
    record = finished.get("content") or {}
    rows = list(record.get("rows") or record.get("hosts") or [])
    summary_path = _relocate(live["run_id"], key, record)
    return {
        "error": finished.get("error"),
        "content": {
            "serial": key,
            "state": "stopped",
            "started_at": live["started_at"],
            "stopped_at": _now(),
            "rows": rows,
            "summary_path": summary_path,
            "note": record.get("skipped") or record.get("note") or "",
        },
    }


def status(serial: str = "", now: float | None = None) -> dict:
    """What is being watched, and for HOW LONG. Reads memory, touches nothing."""
    stamp = _now() if now is None else float(now)
    out = []
    for key, live in _WATCHES.items():
        if serial and _key(serial) != key:
            continue
        age = max(0.0, stamp - float(live["started_at"]))
        out.append(
            {
                "serial": key,
                "state": "expired" if age > MAX_WATCH_S else "watching",
                "age_s": round(age, 1),
                # An expired watch is reported, never silently reaped: the
                # console capture behind it may still be running, and a reader
                # told "nothing is watching" would never stop it.
                "note": (
                    "Older than the watch freshness bound -- stop it; the console "
                    "capture behind it may still be recording."
                )
                if age > MAX_WATCH_S
                else "",
            }
        )
    return {"error": None, "content": {"watches": out}}


async def handle(
    action: str, serial: str = "", package: str = "", apply: bool = False
) -> dict:
    """One entry point, so the action vocabulary lives in ONE place."""
    want = (action or "").strip().lower()
    if want not in ACTIONS:
        return {
            "error": "unknown action: " + want + "; use " + ", ".join(ACTIONS),
            "content": None,
        }
    if want == "start":
        return await start(serial, package, apply)
    if want == "stop":
        return await stop(serial)
    return status(serial)
