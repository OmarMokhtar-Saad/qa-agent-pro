"""The exploratory lane's refusal and extension notices (texts only).

Cut from ``tools.mobile.explore_runner``; the facade re-exports every name here (identity).
"""

from __future__ import annotations

from tools.mobile import finding_nag

EXTENSION_REFUSAL = (
    "An extension needs a stated reason naming what is still unexplored. "
    "Nothing was extended."
)

#: Shown when a turn reply carried no ``finding``, although the packet's
#: ``worker_instructions`` asks for one EVERY turn.
#:
#: It is a DISCLOSURE and not a refusal, and that is the deliberate choice: by
#: the time this runs the turn's actions have already been replayed on the
#: device, so refusing the turn would report a device that DID move as a turn
#: that did not, and boomeranging for a prose field would spend one of the
#: session's stops on it. The report says the same thing from the other end --
#: it counts the turns that recorded nothing -- so the gap stays visible even
#: if this line is ignored in chat.
NO_FINDING_NOTICE = finding_nag.FINDING_REMINDER
EXTENSION_SPENT = (
    "This session has already used its one extension, so the budget stands. "
    "Report what was found and stop."
)

#: Shown when an extension would gain NOTHING, because the charter's own
#: budget is already this run's ceiling. It is a REFUSAL BY NAME, and the
#: session's one extension is NOT spent.
#:
#: Measured on the shipped code before this existed: for ANY charter naming a
#: steps or minutes budget the extension was a COMPLETE no-op -- new_state had
#: already clamped the budget to the charter's figure and the extension
#: re-clamped to that same figure -- while the reply said "Extended once by 15
#: turns and 10 minutes" and the one extension was consumed. The tester's
#: model then plans against fifteen turns it does not have, and the run dies
#: at the next budget check with nothing in the record explaining why.
#:
#: The rule this obeys is the lane's, not this function's: EVERY site that
#: refuses or reduces what the model asked for says so BY NAME in the same
#: reply, and no clamp leaves a success-shaped message behind it -- the same
#: reason a flag-OFF ``apply=true`` on the push path refuses by name rather
#: than silently downgrading to a dry run.
EXTENSION_NOT_GRANTED = (
    "This run's budget comes from its charter, which is already the ceiling, "
    "so an extension would add no turns and no time: nothing was extended and "
    "the session's one extension was NOT spent. Report what was found and "
    "stop, or start a run whose charter names a larger budget."
)

#: The same two refusals, worded for a turn that is NOT actually the run's
#: last one -- `apply_turn_result` picks between this pair and the "stop"
#: pair above by checking `stop_reason` BEFORE choosing the notice. Audit
#: item 2: a run at turn 4 of 30 whose second extension was refused got
#: EXTENSION_SPENT ("...Report what was found and stop.") glued directly
#: ahead of the next packet's own instructions to keep going -- one reply
#: telling the model both to stop and to continue. The text must match the
#: status it ships beside, not just the refusal it names.
EXTENSION_SPENT_CONTINUE = (
    "This session has already used its one extension, so the budget stands "
    "at what it already has. Keep going within that budget and report what "
    "was found when it ends."
)
EXTENSION_NOT_GRANTED_CONTINUE = (
    "This run's budget comes from its charter, which is already the ceiling, "
    "so an extension would add no turns and no time: nothing was extended and "
    "the session's one extension was NOT spent. Keep going within the "
    "charter's budget, or start a run whose charter names a larger budget."
)
