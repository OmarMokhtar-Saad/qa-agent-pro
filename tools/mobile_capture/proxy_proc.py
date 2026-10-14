"""Constants, port/spawn helpers and the runtime marker of the capture proxy.

Leaf of ``tools.mobile_capture.proxy`` (the facade). The ONE home of every
scalar below: a test that patches one patches the name ``proxy_start`` reads.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import time
from pathlib import Path

from tools.mobile_capture import paths, teardown

logger = logging.getLogger("tools.mobile_capture.proxy")

#: How long ONE spawn is waited on before the run continues WITHOUT capture.
#: See tests/test_bounds_upper.py::CEILINGS.
START_TIMEOUT_S: int = 20

#: Concurrently PROXIED devices, one ``mitmdump`` each.
#: See tests/test_bounds_upper.py::CEILINGS.
MAX_LIVE_PROXIES: int = 2

REASON_DEVICE_BUSY = "device_busy"
REASON_TOO_MANY_PROXIES = "too_many_proxies"
REASON_PROXY_START_FAILED = "proxy_start_failed"

#: Loaded by ``mitmdump -s`` as its addon script. Phase 5 (``addon.py``) is
#: what actually writes flows there; this module only needs the path, fixed
#: now so that phase does not have to touch this constant.
ADDON_PATH: Path = Path(__file__).resolve().with_name("addon.py")

HTTP_PROXY_SETTING = "http_proxy"

#: The ONLY channel to the addon's OTHER interpreter (T5.1): it reads these
#: two names from its own environment, never anything imported from this
#: tree. Both are set on the ``mitmdump`` child's environment, never on this
#: process's own -- a leaked env var here would let a later, unrelated call
#: in THIS process pick up a stale run id.
# Off the QA_ prefix on purpose -- see the note beside addon.RUN_ID_ENV.
# These two literals must stay in step with the addon's, which the addon
# contract test asserts rather than trusting.
ADDON_RUN_ID_ENV = "MITMCAP_RUN_ID"
ADDON_DIR_ENV = "MITMCAP_DIR"


def _free_port() -> int:
    """An ephemeral localhost port, free at the instant this returns."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_port_open(port: int, timeout_s: float) -> bool:
    """Blocking poll for *port* to accept a connection. A thin, patchable seam."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            try:
                sock.connect(("127.0.0.1", port))
                return True
            except OSError:
                time.sleep(0.05)
    return False


def spawn_proxy(port: int, ca_dir: Path) -> subprocess.Popen:
    """The ONE place ``mitmdump`` is spawned. ``Popen``, never ``shell=True``."""
    return subprocess.Popen(
        [
            "mitmdump",
            "-q",
            "--listen-host",
            "127.0.0.1",
            "--listen-port",
            str(port),
            "--set",
            "confdir=" + str(ca_dir),
            "-s",
            str(ADDON_PATH),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def read_marker(serial: str) -> dict | None:
    """The runtime marker for *serial*, or ``None``. Never raises."""
    target = paths.proxy_marker(serial)
    try:
        if not target.is_file():
            return None
        body = json.loads(target.read_text(encoding="utf-8"))
        return body if isinstance(body, dict) else None
    except (OSError, ValueError, RecursionError):
        logger.warning("mobile_capture.proxy: unreadable marker for %s", serial)
        return None


def write_marker(serial: str, body: dict) -> None:
    """Write the marker atomically. THE ONE PRODUCER of the marker's shape."""
    target = paths.proxy_marker(serial)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(body, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, target)


def _marker_bodies(root: Path):
    """Every ``proxy-*.json`` under *root* that parses to a dict, in name order."""
    for entry in sorted(root.glob("proxy-*.json")):
        try:
            body = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            continue
        if isinstance(body, dict):
            yield body


# live_markers: the cap check below uses this to keep a caller's OWN slot from
# ever counting against itself.
def live_markers(*, exclude_serial: str = "") -> list[tuple[str, dict]]:
    """``(serial, marker)`` for every marker that is a genuinely LIVE proxy.

    Excludes crash residue (:func:`tools.mobile_capture.teardown.is_crash_residue`
    -- the ONE producer of that judgement, not re-derived here) and, optionally,
    one serial: the cap check uses this to keep a caller's OWN slot from
    ever counting against itself.
    """
    root = paths.capture_root()
    if not root.is_dir():
        return []
    out: list[tuple[str, dict]] = []
    for body in _marker_bodies(root):
        serial = str(body.get("serial") or "")
        if serial and serial != str(exclude_serial):
            if not teardown.is_crash_residue(body):
                out.append((serial, body))
    return out


def _cap_refusal(serial: str) -> dict | None:
    """``None`` when there is room; a refusal naming every holder otherwise.

    NEVER KILLS. Reaching the cap refuses by name (T3.2b); the only path that
    ever tears a live proxy down is crash-residue reaping in ``teardown.py``,
    never this function.
    """
    live = live_markers(exclude_serial=serial)
    if len(live) < MAX_LIVE_PROXIES:
        return None
    holders = [{"serial": s, "run_id": str(b.get("run_id") or "")} for s, b in live]
    return {
        "error": REASON_TOO_MANY_PROXIES,
        "content": {"max": MAX_LIVE_PROXIES, "holders": holders},
    }


def cap_refusal(serial: str) -> dict | None:
    """Public seam for :func:`_cap_refusal`; looked up at call time so a patch bites."""
    return _cap_refusal(serial)
