"""One case's trace across every submit."""

from __future__ import annotations

from typing import Any

from tools.mobile.case_run_consts import MAX_CASE_TRACE


# One case's trace across every submit, renumbered as a single sequence.
#
# It used to be REPLACED on each checkpoint, and the 2026-09-06 live run is
# what that costs: a budget stop -- cheap and therefore common by design --
# left the report's steps table showing only the final script, so the nine
# actions that typed and sent both questions were gone from the evidence the
# report exists to give. A refused script was worse still: it contributed an
# empty list and wiped everything.
#
# WHICH entries the cap drops is the part that matters. The sends are at the
# start and the verdict is at the end, so the MIDDLE goes and a marker says
# how many -- dropping the head would lose exactly the evidence this is for.
def merge_traces(prior: Any, new: Any) -> list:
    """One case's trace across every submit, renumbered as a single sequence."""
    merged = [item for item in list(prior or []) if isinstance(item, dict)]
    merged += [item for item in list(new or []) if isinstance(item, dict)]
    if len(merged) > MAX_CASE_TRACE:
        merged = _trim_trace_middle(merged, (MAX_CASE_TRACE - 1) // 2)
    for index, entry in enumerate(merged):
        entry["index"] = index
    return merged


def _already_dropped(merged: list) -> int:
    """EVERY action ever dropped, as recorded by earlier trim markers.

    Not just this pass's. The count came from the current list, which already
    held the previous marker as a single entry -- so each re-trim forgot what
    the last one dropped and the evidence said 61 where 182 were gone. Budget
    stops are cheap and therefore common, so a second trim is the expected path.
    """
    already = 0
    for entry in merged:
        if str(entry.get("outcome") or "") != "trimmed":
            continue
        try:
            already += int(str(entry.get("detail") or "0").split()[0])
        except (TypeError, ValueError, IndexError):
            continue
    return already


def _trim_marker(dropped: int) -> dict:
    """The single trace entry that says how many middle actions were dropped."""
    return {
        "action": {"op": "..."},
        "outcome": "trimmed",
        "detail": (
            str(dropped) + " actions in the middle of this case were dropped to "
            "bound the trace; the start and the end are kept"
        ),
        "before_screen_id": "",
        "after_screen_id": "",
        "ms": 0,
    }


def _trim_trace_middle(merged: list, keep: int) -> list:
    """Keep *keep* entries at each end and put a marker where the middle was."""
    already = _already_dropped(merged)
    merged = [entry for entry in merged if str(entry.get("outcome") or "") != "trimmed"]
    # No `+ 1`: the marker this pass strips was never an ACTION, so counting
    # it as one over-reported by exactly the number of trims. Caught by the
    # test that compares the marker against the real arithmetic instead of
    # against itself.
    dropped = already + len(merged) - (keep * 2)
    return merged[:keep] + [_trim_marker(dropped)] + merged[-keep:]
