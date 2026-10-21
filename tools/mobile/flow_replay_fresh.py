"""H19: a paused replay is resumed only when it still describes the saved flow.

The replay state (``body["auto_replay"]``) remembers the flow name, the step it
paused at, how many steps the flow had and its route signature. A flow saved again
under the same name with another route (a later run learned it, even with the same
step count) makes that position meaningless, so the old state is stale and a fresh
pick is made instead.
"""

from __future__ import annotations

from tools.mobile import kstg_flow_sig


def same_shape(last: dict, template: dict) -> bool:
    """True when the flow still has the step count and route the paused state recorded."""
    total = last.get("total")
    if not isinstance(total, int):
        return False
    if total != len(template.get("steps") or []):
        return False
    sig = last.get("sig")
    return not sig or sig == kstg_flow_sig.of_template(template)
