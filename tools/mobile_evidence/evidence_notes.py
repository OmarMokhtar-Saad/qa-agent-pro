"""Every note, tolerance and stream name the evidence join prints: the one home."""

from __future__ import annotations

SCHEMA = "qa-agents.mobile-evidence.evidence/1"

#: How far a case window is widened when the device clock was not read (D6).
TOLERANCE_MS = 2000

#: Everything the parser emits per turn, in the shape the report reads.
STREAMS = (
    "llm",
    "bindings",
    "tools",
    "notes",
    "errors",
    "utterances",
    "answers",
    "flowStates",
    "cards",
    "runlog",
)

DERIVED_NOTE = (
    "Not a stopwatch reading. The app writes a line when it sends the request and another "
    "when the answer arrives, and this is the gap between those two lines -- so it also "
    "contains anything else the app did in between"
)

AMBIGUOUS_NOTE = "ambiguous -- could belong to an adjacent case"
NO_CLOCK_NOTE = "device clock not read -- attribution by host clock, +/-%d s" % (
    TOLERANCE_MS // 1000
)
ONE_TURN_NOTE = (
    "the profile carries no turn-start marker, so every record inside this case's window "
    "is shown as one turn per case"
)

#: The same fallback, reached for a DIFFERENT reason, so it says a different
#: thing. The log had turns, but every one of them belongs to an app session
#: that is not this run -- an app log holds every session it ever wrote, and the
#: measured file carried thirty-nine older ones. Saying "no turn-start marker"
#: here would be false.
NO_TURN_IN_ANY_CASE_NOTE = (
    "the app log carries turns, but none of them falls inside a case of this run "
    "-- they belong to earlier sessions of the app -- so every record inside a "
    "case's window is shown as one turn per case"
)


INVERTED_NOTE = (
    "this case ran inside a neighbour's window, so its own window was clipped to a "
    "point -- the app's events in that span are attributed to the neighbour"
)
