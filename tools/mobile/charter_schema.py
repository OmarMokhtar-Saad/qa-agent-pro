"""The charter schema: keys, closed enums, aliases and the fresh defaults.

Split from :mod:`tools.mobile.charter`, which re-exports every name here.
"""

from __future__ import annotations

#: Where :func:`normalize` records WHICH fields the tester left unanswered.
#: It is metadata about the answering, not a term of the run, so it is not a
#: key of :func:`defaults`; it is carried in the normalised charter because the
#: verdict can only be computed from the RAW input and every later reader has
#: only the normalised one.
DEFAULTS_KEY = "defaults_used"

#: Where :func:`normalize` records the fields the tester DID answer whose
#: answer a coercer REPLACED. Same channel, same strip and the same anti-spoof
#: argument as :data:`DEFAULTS_KEY`: it is this module's own output, so
#: :func:`normalize` drops any copy a host sends before deriving its own.
REJECTED_KEY = "rejected_fields"

#: The fields whose value a coercer can SILENTLY SUBSTITUTE -- the closed
#: enums and the two budget axes. Everything else is free text that is
#: bounded, never replaced.
REJECTABLE = ("depth", "destructive", "stop_on", "budget.steps", "budget.minutes")

DEPTHS = ("happy", "negative", "both")
DESTRUCTIVE = ("none", "reversible", "allowed")
STOP_ON = ("first_finding", "budget", "coverage_plateau")

#: Answers accepted as a synonym for the field's real enum value, resolved in
#: :func:`_choice` before the allowed-set check runs -- so every call site
#: (`normalize`'s depth/destructive/stop_on clamps and the standalone
#: `stop_on` reader) gets the resolution for free, and in `_rejected`'s own
#: comparison, so an alias is never misreported as a value the server had to
#: replace.
ALIASES = {"happy_path": "happy"}

#: What a guard hit DOES under this charter. Two values, and only two, and
#: BOTH are implemented: ``PAUSE`` stops for the tester and ``REFUSE`` ends
#: the attempt by name -- see :func:`guard_policy` for which value maps where
#: and for the one site that reads it.
PAUSE = "pause"
REFUSE = "refuse"

#: The stop conditions step 4 actually WIRES. ``coverage_plateau`` is absent on
#: purpose and :func:`describe_terms` discloses it.
WIRED_STOPS = ("first_finding", "budget")


def defaults() -> dict:
    """A FRESH default charter. A function, so no caller can mutate the table."""
    return {
        "goal": "",
        "depth": "both",
        "scope": {"include": [], "exclude": []},
        "destructive": "none",
        "budget": {"steps": 0, "minutes": 0},
        "stop_on": "budget",
    }
