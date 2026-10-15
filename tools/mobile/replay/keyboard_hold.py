"""Replay engine: the QA keyboard held for a whole run instead of per script.

A run replays many short scripts (one step each in a flow replay). Switching the
input method up and down around every one costs a round trip each time and
nothing is gained: the tester's keyboard is remembered once in the run's IME
record and given back from it when the run ends (``finish_device``), when a
script stops for the tester, errors or is cancelled (``loop_keyboard``), and
when the run is stopped or released idle (``session_finish.release_restoring``).
A killed process restores nothing: the next run on the device sweeps the record.
"""

from __future__ import annotations

from tools.mobile import ime, ime_session
from tools.mobile.imes_remember import _record_body

#: ``serial -> run_id`` of the run that holds the QA keyboard on that device.
_HELD: dict[str, str] = {}


def is_held(serial: str, run_id: str) -> bool:
    """True when *run_id* already has the QA keyboard selected on *serial*."""
    return bool(run_id) and _HELD.get(str(serial)) == run_id


def forget(run_id: str) -> None:
    """Drop every hold of *run_id*, by run (its device may already be released)."""
    for serial in [s for s, held in _HELD.items() if held == str(run_id)]:
        _HELD.pop(serial, None)


def previous(run_id: str) -> str | None:
    """The tester's keyboard the run's record remembers; None without a record."""
    record = ime_session.read_record(run_id) or {}
    return str(record["previous"]) if record.get("previous") else None


def _kept(serial: str, run_id: str, previous: str) -> dict:
    """The run's IME record, written when none exists yet; an error shape on failure."""
    if ime_session.read_record(run_id):
        return {"error": None}
    ours = str(((ime.manifest() or {}).get("content") or {}).get("ime_id") or "")
    kept = {"previous": previous, "was_ours": ime.same_component(previous, ours)}
    return ime_session.write_record(run_id, _record_body(serial, kept))


def hold(serial: str, run_id: str, previous: str | None) -> bool:
    """Keep the QA keyboard for the run; False when it could not be made restorable."""
    if not run_id or previous is None:
        return False
    if _kept(serial, run_id, str(previous)).get("error"):
        return False
    _HELD[str(serial)] = run_id
    return True
