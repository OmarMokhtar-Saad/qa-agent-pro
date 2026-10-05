"""Record how a run ended: ONE verdict in the manifest, written once.

``qa_mobile_stop`` and the idle release both end a run from outside its own
chat. Each needs the same durable fact (the verdict, and why the run stopped),
so :func:`finalize` is the one writer of ``manifest["final"]``.

IDEMPOTENT: a manifest that already has a ``final`` is left alone and ``False``
comes back, so a double stop, or an idle release racing a stop, records one
verdict. NOT atomic: it is a whole-manifest read-modify-write, like every other
manifest writer in ``run_store``.

On the explore lane an unset ``explore["stop"]`` is set to the stop code, which
``explore_runner.stop_reason`` then honours: a run stopped from outside takes no
further turn. A failure RAISES ``RuntimeError`` (the port contract of
``stop_run`` and ``lock_reaper``), so "could not record its verdict" is
reported instead of a success-shaped reply.
"""

from __future__ import annotations

from tools.mobile import run_store

#: Written as ``final.stop`` when the tester ended the run through ``qa_mobile_stop``.
STOP_TESTER = "stopped_by_tester"
#: Written when a new run took the device from a holder that had gone idle.
STOP_IDLE = "released_idle"

#: Characters of any one recorded text field; the verdict reason is short already.
MAX_TEXT_CHARS = 300
#: Evidence lines kept in ``final``.
MAX_EVIDENCE = 20

# ``session.LANE_EXPLORE`` cannot be imported here: session imports this module.
_LANE_EXPLORE = "explore"


def _clip(value: object) -> str:
    return " ".join(str(value or "").split())[:MAX_TEXT_CHARS]


def _final_of(verdict: object, stop: str) -> dict:
    evidence = getattr(verdict, "evidence", None)
    lines = evidence if isinstance(evidence, (list, tuple)) else ()
    return {
        "outcome": _clip(getattr(verdict, "outcome", "")),
        "reason": _clip(getattr(verdict, "reason", "")),
        "evidence": [_clip(line) for line in lines[:MAX_EVIDENCE]],
        "stop": _clip(stop),
    }


def _read(run_id: str) -> dict:
    read = run_store.read_manifest(run_id)
    body = read.get("content")
    if read.get("error") or not isinstance(body, dict):
        raise RuntimeError("the run manifest could not be read")
    return body


def _mark_explore(manifest: dict, stop: str) -> None:
    explore = manifest.get("explore")
    if manifest.get("lane") != _LANE_EXPLORE or not isinstance(explore, dict):
        return
    if stop and not explore.get("stop"):
        explore["stop"] = _clip(stop)


def finalize(run_id: str, verdict: object, *, stop: str = "") -> bool:
    """Record ``verdict`` as the run\'s final outcome; True when THIS call wrote it.

    False means the run already had a final verdict and nothing changed. Raises
    ``RuntimeError`` when the manifest cannot be read or written.
    """
    manifest = _read(run_id)
    if isinstance(manifest.get("final"), dict):
        return False
    manifest["final"] = _final_of(verdict, stop)
    _mark_explore(manifest, stop)
    if run_store.write_manifest(run_id, manifest).get("error"):
        raise RuntimeError("the run verdict could not be written")
    return True


def stop_text(manifest: object) -> str:
    """The recorded stop code of a finished run, or ``""``. Total: never raises."""
    final = manifest.get("final") if isinstance(manifest, dict) else None
    return str(final.get("stop") or "") if isinstance(final, dict) else ""


def recorded(manifest: object) -> dict:
    """The recorded final verdict of a finished run, or ``{}``. Total: never raises."""
    final = manifest.get("final") if isinstance(manifest, dict) else None
    return final if isinstance(final, dict) else {}
