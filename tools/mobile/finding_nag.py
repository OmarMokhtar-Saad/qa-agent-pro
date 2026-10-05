"""When to remind the model that a turn recorded no ``finding``.

The reminder used to ride every turn that carried none, which spent a sentence
of the model's attention on each round trip. It is now shown only on the LAST
turn of the budget, or when the run is stopping, which is when a missing finding
actually costs the report something. Pure functions; the wiring stream decides
where the notice is shown.
"""

from __future__ import annotations

FINDING_REMINDER = (
    "This step recorded no `finding`, so the report has nothing to show for it. "
    "Add one sentence with your final reply, even when everything went fine."
)


def should_remind(turn: int, max_turns: int, final: bool = False) -> bool:
    """True only on the last turn (``turn >= max_turns``) or when *final*."""
    if final:
        return True
    return int(max_turns or 0) > 0 and int(turn or 0) >= int(max_turns)


def reminder(
    turn: int, max_turns: int, *, final: bool = False, finding: object = ""
) -> str:
    """The reminder text, or ``""`` when a finding was given or it is not time."""
    if isinstance(finding, str) and finding.strip():
        return ""
    return FINDING_REMINDER if should_remind(turn, max_turns, final) else ""
