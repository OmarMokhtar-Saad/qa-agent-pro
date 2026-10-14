"""Starting a watch: the refusals, the capture call and the registration.

The lane's own gate and ``capture.begin_network`` are looked up on their modules
at call time, so a test that patches either one bites here.
"""

from __future__ import annotations

from tools.mobile_evidence import capture, capture_gate
from tools.mobile_evidence.network_watch_key import _key, _serial_refusal, _watch_id
from tools.mobile_evidence.network_watch_state import _WATCHES, _now

_NEEDS_APPLY = (
    "Refusing to start a network watch without `apply=true`. Starting "
    "asks the emulator console to record and samples the device's "
    "socket table; stopping needs no acknowledgement, because a "
    "teardown a tester cannot run is how a capture gets left running."
)


def _watching(key: str, *, already: bool) -> dict:
    return {
        "error": None,
        "content": {
            "serial": key,
            "state": "watching",
            "already": already,
            "started_at": _WATCHES[key]["started_at"],
        },
    }


def _refusal_for(serial: str, apply: bool) -> dict | None:
    """The answer that refuses this start, or None when it may go ahead."""

    refusal = capture_gate.gate_refusal()
    if refusal:
        return {"error": refusal, "content": None}
    if not _key(serial):
        return {"error": _serial_refusal(serial), "content": None}
    if not apply:
        return {"error": _NEEDS_APPLY, "content": None}
    return None


def _register(key: str, live: dict) -> None:
    _WATCHES[key] = live


async def _launch(key: str, package: str) -> dict | None:
    """Begin the capture; an answer to return as it is, or None once registered."""

    run_id = _watch_id(key)
    req = capture.BeginReq(key, str(package or ""), run_id, "watch", 0)
    begun = await capture.begin_network(req)
    if begun.get("error"):
        return begun
    record = begun.get("content") or {}
    if not record.get("started"):
        # A skip is an ANSWER -- "this is a phone", "the console said no" --
        # and it is returned with its own stage rather than reworded here.
        return {"error": None, "content": dict(record, serial=key, state="not started")}
    live = {"run_id": run_id, "package": str(package or ""), "begun": record}
    _register(key, dict(live, started_at=_now()))
    return None


async def start(serial: str, package: str = "", apply: bool = False) -> dict:
    """Begin a watch on ``serial``. Needs ``apply=true``: it touches the device."""
    refusal = _refusal_for(serial, apply)
    if refusal:
        return refusal
    key = _key(serial)
    if key in _WATCHES:
        # `already` is a FIELD, not a wording difference: a double-clicked
        # Start must not read to a caller as a fresh capture beginning now,
        # because the rows it eventually gets will reach back to the first one.
        return _watching(key, already=True)
    answer = await _launch(key, package)
    return answer if answer is not None else _watching(key, already=False)
