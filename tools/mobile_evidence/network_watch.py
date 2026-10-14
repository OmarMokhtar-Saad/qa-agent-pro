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
  ``capture_gate.gate_refusal()`` is called for every action, so a flag-off install
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

import logging
from typing import NamedTuple

from tools.mobile_evidence.network_watch_key import (  # noqa: F401
    _SERIAL_RE,
    WATCH_RUN_PREFIX,
    _key,
    _serial_refusal,
    _under,
    _watch_id,
)
from tools.mobile_evidence.network_watch_start import _watching, start  # noqa: F401
from tools.mobile_evidence.network_watch_state import (  # noqa: F401
    _WATCHES,
    ACTIONS,
    MAX_WATCH_S,
    _now,
    status,
)
from tools.mobile_evidence.network_watch_stop import (  # noqa: F401
    _relocate,
    stop,
    watch_dir,
)

# _now and MAX_WATCH_S above are read-only aliases: the leaves bind them from
# network_watch_state, so patch them there, not on this facade.
logger = logging.getLogger(__name__)


class WatchTarget(NamedTuple):
    """Which device a watch action is about, and whether it may touch it."""

    serial: str = ""
    package: str = ""
    apply: bool = False


async def handle(action: str, target: WatchTarget = WatchTarget()) -> dict:
    """One entry point, so the action vocabulary lives in ONE place."""
    want = (action or "").strip().lower()
    if want not in ACTIONS:
        return {
            "error": "unknown action: " + want + "; use " + ", ".join(ACTIONS),
            "content": None,
        }
    if want == "start":
        return await start(target.serial, target.package, target.apply)
    if want == "stop":
        return await stop(target.serial)
    return status(target.serial)
