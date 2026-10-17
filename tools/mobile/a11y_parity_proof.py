"""A remembered arming parity proof, so a proven device skips the uiautomator reads.

Measured on run mrun-20261017-221135-970721: the arming parity check cost two
uiautomator dumps (11.2 s and 11.8 s) of a 70 s pre-run, on a device whose a11y
reader had passed the same check on every run that day. Parity is a property of the
device build and the qa-ime build, so a pass is kept per serial with both, and reused
for :data:`PROOF_TTL_S`. A new Android build, a new qa-ime or an expired proof runs
the full check again; a refusal forgets the proof. Per-dump freshness checks
(``a11y_fresh``) still run on every read.
"""

from __future__ import annotations

import json
import logging
import os
import time

from tools.mobile import a11y_settle, adb, imes_upgrade, paths
from tools.mobile.ime_host import host

logger = logging.getLogger("tools.mobile.a11y")

STATE_FILE = "a11y-parity.json"

#: Seconds a parity pass is reused. Bounded from above in
#: ``tests/mobile/test_mobile_bounds_upper.py``.
PROOF_TTL_S = 7 * 24 * 3600

#: Devices remembered; the oldest proof is dropped first.
MAX_PROOFS = 32


async def identity(serial: str) -> str:
    """``<android build>|<qa-ime versionName>``, ``""`` when either is unreadable."""
    got = await adb.shell(serial, ["getprop", "ro.build.fingerprint"])
    build = str((got.get("content") or {}).get("out") or "").strip()
    package = str((host().manifest().get("content") or {}).get("package") or "")
    ime = await imes_upgrade.installed_version(serial, package) if package else ""
    return f"{build}|{ime}" if build and ime and not got.get("error") else ""


def _load() -> dict:
    try:
        raw = json.loads(paths.state_file(STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save(proofs: dict) -> None:
    path = paths.state_file(STATE_FILE)
    tmp = path.with_name(path.name + ".tmp")
    kept = dict(sorted(proofs.items(), key=lambda kv: kv[1].get("at", 0))[-MAX_PROOFS:])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(kept), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        logger.warning("a11y_parity_proof: could not write %s", path, exc_info=True)


async def proven(serial: str) -> bool:
    """True when *serial* passed parity on this build and qa-ime within the TTL."""
    entry = _load().get(str(serial or ""))
    if not isinstance(entry, dict):
        return False
    fresh = time.time() - float(entry.get("at") or 0) < PROOF_TTL_S
    return fresh and bool(entry.get("id")) and entry["id"] == await identity(serial)


async def remember(serial: str) -> None:
    """Keep a parity pass for *serial*; nothing is kept when the identity is unreadable."""
    ident = await identity(serial)
    if not ident:
        return
    proofs = _load()
    proofs[str(serial or "")] = {"id": ident, "at": time.time()}
    _save(proofs)


def forget(serial: str) -> None:
    proofs = _load()
    if proofs.pop(str(serial or ""), None) is not None:
        _save(proofs)


async def checked(serial: str, fast_xml: str) -> dict:
    """The settled parity check, skipped while *serial* holds a proof."""
    if await proven(serial):
        logger.info("mobile.a11y: parity proven earlier on %s; skipped", serial)
        return {"error": None, "content": {"parity": "remembered"}}
    got = await a11y_settle.checked(serial, fast_xml)
    if got.get("error"):
        forget(serial)
    else:
        await remember(serial)
    return got
