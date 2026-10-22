"""The protected finalize-reply headline that contradicts an empty duplicate review."""

from __future__ import annotations


def _headline_cap_note(n: int, total) -> str:
    """NO SILENT CAPS: when _DUP_SHORTLIST_MAX_PAIRS truncated the list, say so
    in the same breath as the count. A saturated list read as a complete one
    is exactly how the F08 replay was misread the first time."""
    try:
        if int(total) > n:
            return (
                f" {int(total)} pair(s) cleared the bar in all; only the "
                f"closest {n} are listed."
            )
    except (TypeError, ValueError, OverflowError):
        pass
    return ""


# The PROTECTED finalize-reply CLAIM: the host's empty duplicate review is
# contradicted by the server's own prescreen. "" when nothing was found.
#
# D4 (2026-08-21). On the TICKET-5646 run the host finalized through the merged
# route with ``duplicate_groups: []``, so the server recorded "review ran,
# none found" -- an assurance that was false over a suite in which a reviewer
# found nine redundant clusters. The prescreen that could have contradicted it
# already existed and was wired to ``qa_submit_category`` only, so it never
# ran on the route this suite took.
#
# Bounded by construction at 496 chars, which is what lets it afford to be a
# PROTECTED reply section. The EVIDENCE (the pair list) is a SEPARATE and
# TRIMMABLE section, ``build_dup_contradiction_pairs``: the claim about
# whether the deliverable is what it appears to be must survive the reply
# budget, the list backing it need not. See the reply-budget section of
# the D4/D5 plan for the measurements.
#
# HONESTY BOUND, stated here because it is the reason the wording is a FLOOR
# rather than a finding. Measured on the stored TICKET-5646 suite (96 cases,
# the run that produced this fix) against nine redundant clusters covering 33
# case pairs: this prescreen returns FIVE pairs and recovers exactly ONE
# cluster (TC-084 / TC-095, ratio 0.667). Four of the five are deliberate
# variants -- English/Arabic, Shipped/Delivered, Pending/Cancelled -- i.e.
# correctly distinct cases. Lowering DUP_SHORTLIST_MIN_RATIO does NOT rescue
# it and was not done: 0.70 recovers none, 0.60 and 0.55 recover two of nine,
# and 0.30 recovers five of nine only by emitting forty-four pairs at 23%
# precision. That is the same shape F08 measured when it rejected
# threshold-tuning. So this text says "check these", never "remove these", and
# it says out loud that an EMPTY prescreen is not evidence either.
#
# Pure, synchronous, stdlib only. Never raises.
def build_dup_contradiction_headline(found: int, total: int) -> str:
    """The PROTECTED finalize-reply CLAIM: the host's empty duplicate review is contradicted by the prescreen."""
    try:
        n = int(found)
    except (TypeError, ValueError, OverflowError):
        return ""
    if n <= 0:
        return ""
    more = _headline_cap_note(n, total)
    return (
        "> \u267b\ufe0f  **That duplicate review is CONTRADICTED by the "
        "server's own prescreen.** You reported no cross-category duplicates; "
        "a stdlib shared-word comparison of the FINAL case titles found "
        f"{n} candidate pair(s).{more} It is a FLOOR, not a measurement: "
        "replayed on a real 96-case suite in which a reviewer found nine "
        "redundant clusters it recovered ONE, so an empty prescreen is not "
        "evidence of a clean suite either. Nothing was removed.\n\n"
    )
