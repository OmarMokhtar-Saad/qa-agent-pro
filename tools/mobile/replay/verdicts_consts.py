"""Replay engine: verdict detail strings and caps. Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

#: What the model is told when a TARGETED scroll's target did not resolve.
#: ``scroll`` is the one op whose target miss is not handed back -- panning is a
#: sensible default and is what a tester would do -- but the degradation was
#: SILENT: measured, a resolved scroll, a stale-target scroll and a target-less
#: pan all reported ``no_change`` with an empty detail, so the model could not
#: tell that the element it named was gone and planned its next turn as though
#: it had scrolled it. The stop itself is unchanged; only the record is.
#:
#: "DID NOT RESOLVE", never "was not on this screen". ``resolve_target``
#: answers None for a CONFLICT and for an ambiguous ROLE as well as for a plain
#: miss, and in both of those the elements ARE present -- which is why
#: :func:`missing_element_detail` puts its ambiguity branch first and says as
#: much. A note claiming absence would be false in two of the five cases.
#:
#: It carries no per-case remedy, and that is a length decision made against
#: two hard caps downstream: ``report.MAX_TEXT`` (200) truncates this string in
#: the HTML row, and ``mobile_run._trace_block`` caps it at 300 in the escape
#: packet -- the surface it exists for. The 285-character first draft was cut
#: mid-sentence by the first of them and left 15 characters for the device's
#: own detail. Unlike a boomerang the action ALREADY HAPPENED, and the reply
#: carries the new screen, so the remedy is the same whatever the miss was.
SCROLL_TARGET_MISSED = (
    "That target did not resolve, so the screen was panned instead of the "
    "element you named. Re-read the screen before planning from it."
)

ROUTE_MISMATCH_DETAIL = (
    "This screen is not the one this saved route was recorded from, so nothing was tapped "
    "for this step. Carry on from the packet."
)

# The note is capped at 300 by `AssertAction`; this bounds the prefixed
# detail so a later cap change cannot grow the trace unseen.
MAX_VISUAL_DETAIL_CHARS = 320

#: What ``am start`` prints when the activity was not started because its
#: task already existed and was only moved to the front.
BROUGHT_TO_FRONT = "brought to the front"
