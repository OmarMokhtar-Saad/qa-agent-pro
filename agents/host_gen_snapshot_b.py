"""Host mode: condensed-ticket detector of the prepare pipeline (C3c).

Moved from handlers/generate.py: the F9 thresholds and the CONDENSED warning.
The store is read here by attribute, so patches on ``tools.prep_store`` bite.
"""

from __future__ import annotations

import logging
import time

from agents.host_gen_args import CondensedWarnArgs
from agents.host_gen_snapshot_a import safe_snapshot_stamp
from config.settings_guard import never_raise
from tools import prep_store, settings_port

logger = logging.getLogger("agents.host_mode")

# F9 thresholds. A payload must be BOTH proportionally and absolutely smaller
# before anything is said, so neither a long ticket losing a sentence nor a
# two-line ticket losing a word can trip it.
#
# MEASURED, not guessed: the reported incident is 4,262 chars against 8,600+ for
# the same ticket -- a ratio of 0.50 and a delta of 4,338 -- so 0.80 / 500 sits
# an order of magnitude inside the real signal while leaving room for the
# genuinely benign differences the SAME revision can still produce (a client
# that renders an ADF table with different cell padding, or normalises
# whitespace).
_CONDENSED_MAX_RATIO = 0.80
_CONDENSED_MIN_DELTA = 500


# WARN when the SAME ticket revision came back materially SMALLER than a
# prior run of this install already saw. "" in every other case.
#
# F9 (2026-08-15). ``tools/jira_mcp``'s fetch directive tells the host "Do NOT
# summarise, reword, translate or truncate it", and a host ignored that: it
# handed back a condensed ticket (4,262 stored chars against 8,600+ for the
# same ticket on another run) and the server had no way to tell. It showed up
# downstream as the DF04 status matrix collapsing to ``Received`` alone,
# losing the Shipped and Delivered rows that two other requirements depend on.
#
# WHY THIS COMPARISON AND NOT A DIRECT ONE: this server never fetches the
# ticket. ``jira_mcp`` returns a fetch DIRECTIVE and the host's own Atlassian
# MCP connection does the read, so there is no server-side original to diff
# against -- which is exactly why asking the host to confirm its own fidelity
# is worthless. ``fields.updated`` is the lever: it is Jira's own revision
# stamp, so two payloads carrying the SAME ``updated`` value describe
# BYTE-IDENTICAL ticket content by definition. Any material size difference
# between them was introduced by whatever sat between Jira and this server. No
# model is asked anything; this is arithmetic on two integers.
#
# KNOWN LIMIT, stated rather than papered over: it needs a PRIOR prep for the
# same ticket revision, so it cannot fire on the first run of a ticket. Every
# prep now stamps its size, so the baseline exists from the first run onward
# -- which is precisely the shape of the reported incident, where the same
# ticket was prepared more than once. A first-run condensation still passes
# silently; detecting THAT would need a second independent read of the ticket.
#
# Deliberately a WARNING and never a refusal: it is a size heuristic over
# host-supplied text, and the honest response to "this looks truncated" is to
# tell the tester, not to destroy the run. Never raises.
async def condensed_payload_note(source_text: str, updated: str, chars: int) -> str:
    """WARN when the SAME ticket revision came back materially SMALLER than a
    prior run of this install already saw. "" in every other case. Never raises.
    """
    if not source_text or not updated or chars <= 0:
        return ""
    with never_raise(logger, "condensed-payload check failed", exc_info=True):
        hit = await prep_store.find_prep_snapshot_by_source(
            source_text,
            float(getattr(settings_port.get(), "qa_prep_ttl_s", 86400) or 0),
        )
        prior = (hit or {}).get("content") or {}
        return condensed_note_from_prior(prior, chars, updated)
    return ""


def condensed_note_from_prior(prior: dict, chars: int, updated: str) -> str:
    """The CONDENSED warning when *prior* (a stored prep) shows a material
    shrink for the SAME revision, else ""."""
    prior_chars = int(prior.get("content_chars") or 0)
    prior_stamp = safe_snapshot_stamp(prior.get("jira_updated"))
    # SAME revision only. A different `updated` means the ticket itself
    # changed, and a genuinely shortened ticket is not a defect.
    if not prior_stamp or prior_stamp != updated or prior_chars <= 0:
        return ""
    if chars > prior_chars * _CONDENSED_MAX_RATIO:
        return ""
    if (prior_chars - chars) < _CONDENSED_MIN_DELTA:
        return ""
    when = time.strftime("%H:%M", time.localtime(float(prior.get("created_at") or 0)))
    return condensed_payload_warning(
        CondensedWarnArgs(chars, updated, prior_chars, when)
    )


def condensed_payload_warning(args: CondensedWarnArgs) -> str:
    """The CONDENSED-ticket warning for ``args.chars`` now against ``args.prior_chars``."""
    chars, updated, prior_chars, when = args
    pct = int(round(100 - (chars * 100.0 / prior_chars)))
    return (
        "> \u26a0\ufe0f  **The ticket text looks CONDENSED.** This payload "
        f"carries {chars:,} characters of description and acceptance "
        f"criteria; the same ticket revision (`updated` = `{updated}`) "
        f"yielded {prior_chars:,} at {when} on this install -- about {pct}% "
        "less text for content Jira says is unchanged, so the difference "
        "was introduced between Jira and here. The `getJiraIssue` result "
        "must be passed through RAW: do not summarise, reword, translate or "
        "truncate it, and do not re-render it as prose. Re-run the fetch "
        "and prepare again. Generation continues with what you sent, but "
        "requirements that only exist in the missing text -- table rows, "
        "status lists, edge cases -- cannot be covered."
    )
