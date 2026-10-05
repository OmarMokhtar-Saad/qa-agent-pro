"""uiautomator2 tree source: a persistent on-device server instead of one
``uiautomator dump`` process per read.

OPT-IN PER SERIAL, NEVER INSTALLING ON ITS OWN. ``available`` and ``dump`` both
require ``tree_optin.is_enabled(serial)``, which only a ``UserConsent`` from
``user_confirm`` can set; ``dump`` re-checks it before ANY ``adb install``. The
optional ``uiautomator2`` package is used only to find its bundled helper APKs
and is never imported or installed here.

UNVERIFIED (written from memory of the uiautomator2 project, not run against a
device in this repo): the APK file names, the instrumentation command line, the
device port 9008, the JSON-RPC path and the ``dumpWindowHierarchy`` method and
its params. Each sits in a constant below and behind the TreeSource interface,
so a wrong guess ends in ``None`` and the executor reads the adb dump instead.
To be confirmed by the real-device verification script.

The local port forward needs ``adb.forward``, which does not exist yet (adb.py
is another stream's file); until it does, ``dump`` returns None and nothing
changes for the tester.
"""

from __future__ import annotations

import asyncio
import http.client
import importlib.util
import json
import logging
import socket
import time
import urllib.request
from pathlib import Path
from typing import Awaitable, Callable, Optional

from tools.mobile import adb, tree_optin
from tools.mobile.tree_source import TreeResult

logger = logging.getLogger(__name__)

#: Seconds allowed to install, start and forward the helper, once per serial.
U2_START_TIMEOUT_S = 20

#: After a failure the source steps aside for this long, so a device where the
#: helper does not work pays for one failed attempt, not one per dump.
RETRY_AFTER_S = 120

LOOPBACK = "127.0.0.1"

# UNVERIFIED: see the module docstring.
U2_DEVICE_PORT = 9008
U2_RPC_PATH = "/jsonrpc/0"
U2_RPC_METHOD = "dumpWindowHierarchy"
U2_RPC_PARAMS = [False, 50]
U2_APK_NAMES = ("app-uiautomator.apk", "app-uiautomator-test.apk")
U2_INSTRUMENT = (
    "am",
    "instrument",
    "-r",
    "-e",
    "debug",
    "false",
    "-e",
    "class",
    "com.github.uiautomator.stub.Stub",
    "com.github.uiautomator.test/androidx.test.runner.AndroidJUnitRunner",
)

#: What a helper read may fail with. CancelledError is not here: a cancelled
#: dump must stay cancelled.
FAILURES = (
    OSError,
    ValueError,
    RuntimeError,
    asyncio.TimeoutError,
    http.client.HTTPException,
)

Rpc = Callable[[int, float], Awaitable[Optional[str]]]


def _assets_dir() -> Optional[Path]:
    """The directory holding the helper APKs of an INSTALLED uiautomator2, or None."""
    try:
        spec = importlib.util.find_spec("uiautomator2")
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.origin:
        return None
    assets = Path(spec.origin).parent / "assets"
    return assets if all((assets / n).is_file() for n in U2_APK_NAMES) else None


def _pick_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((LOOPBACK, 0))
        return int(probe.getsockname()[1])


def _post_rpc(port: int, timeout_s: float) -> Optional[str]:
    """One loopback JSON-RPC call; the hierarchy text or None. Blocking."""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": U2_RPC_METHOD,
            "params": U2_RPC_PARAMS,
        }
    ).encode("utf-8")
    url = "http://" + LOOPBACK + ":" + str(int(port)) + U2_RPC_PATH
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as reply:
        raw = reply.read(adb.MAX_DUMP_BYTES + 1)
    if len(raw) > adb.MAX_DUMP_BYTES:
        return None
    data = json.loads(raw.decode("utf-8", "replace"))
    result = data.get("result") if isinstance(data, dict) else None
    return result if isinstance(result, str) and result.strip() else None


async def _rpc(port: int, timeout_s: float) -> Optional[str]:
    return await asyncio.to_thread(_post_rpc, port, timeout_s)


class U2TreeSource:
    name = tree_optin.U2_SOURCE

    def __init__(self, rpc: Optional[Rpc] = None) -> None:
        self._rpc = rpc or _rpc
        self._ports: dict = {}
        self._cooldown: dict = {}

    async def available(self, serial: str) -> bool:
        if not tree_optin.is_enabled(serial) or _assets_dir() is None:
            return False
        return time.monotonic() >= self._cooldown.get(serial, 0.0)

    async def dump(self, serial: str, timeout_s: float) -> Optional[TreeResult]:
        # Consent is re-checked HERE, before any install, not trusted from
        # whoever selected this source.
        if not tree_optin.is_enabled(serial):
            return None
        started = time.monotonic()
        try:
            port = await self._ready(serial)
            xml = await self._rpc(port, timeout_s) if port else None
        except FAILURES:
            logger.warning("u2_tree_source: read failed on %s", serial, exc_info=True)
            xml = None
        if not xml:
            self._fail(serial)
            return None
        elapsed = int((time.monotonic() - started) * 1000)
        return TreeResult(xml=xml, elapsed_ms=elapsed, source=self.name)

    async def _ready(self, serial: str) -> Optional[int]:
        port = self._ports.get(serial)
        if port:
            return port
        port = await asyncio.wait_for(self._start(serial), U2_START_TIMEOUT_S)
        if port:
            self._ports[serial] = port
            self._trim(self._ports)
        return port

    async def _start(self, serial: str) -> Optional[int]:
        assets = _assets_dir()
        forward = getattr(adb, "forward", None)
        if assets is None or forward is None:
            return None
        for name in U2_APK_NAMES:
            if (await adb.install(serial, str(assets / name))).get("error"):
                return None
        if (await adb.shell(serial, list(U2_INSTRUMENT))).get("error"):
            return None
        port = _pick_port()
        remote = "tcp:" + str(U2_DEVICE_PORT)
        if (await forward(serial, "tcp:" + str(port), remote)).get("error"):
            return None
        return port

    def _fail(self, serial: str) -> None:
        self._ports.pop(serial, None)
        self._cooldown[serial] = time.monotonic() + RETRY_AFTER_S
        self._trim(self._cooldown)

    @staticmethod
    def _trim(table: dict) -> None:
        while len(table) > tree_optin.MAX_OPTIN_SERIALS:
            del table[next(iter(table))]


def register_u2_source() -> None:
    """Register at priority 10 (tried before the fallback). Selection still
    needs ``tree_optin.allowed``, so registering opts nobody in."""
    from tools.mobile import tree_source

    tree_source.register_tree_source(U2TreeSource(), priority=10)
