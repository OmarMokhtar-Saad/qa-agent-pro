"""Host-facing script-planning text, one canonical copy (leaf module).

The host LLM pays one turn per submit, so a login that takes seven turns
instead of two is the cost this text removes. Every place that tells the host
how to write a script (the mobile brief, the MCP prompt, the packets, the
step-format help) quotes these blocks, so the cap and the advice cannot drift.
Research on batching agent actions (arXiv 2609.02042) found fewer decision
rounds help but chunks of 5-6 actions hurt success: hence about 3-4 actions
per screen with a check at the end.
"""

from __future__ import annotations

from tools.mobile.action_consts import MAX_MODEL_ACTIONS

PATH_PLAN = (
    "Plan the whole likely path, not only this screen: up to "
    + str(MAX_MODEL_ACTIONS)
    + " actions per submit, about 3-4 per screen. After a tap that opens a new "
    "page keep going with actions for the next screen, targeting by role, rid, "
    "label or text (element ids belong to the screen you were given). Use "
    "tap_text_if_present for screens that may not show (onboarding, Skip, Not "
    "now). End the script with an assert or wait that proves the goal. Do not "
    "plan a destructive or ask_tester step blind past the screen edge."
)

STOP_NOTE = (
    "A script stops at a missing target (the server first waits briefly for it "
    "after a screen change), the wrong screen, a guard, an unknown system "
    "dialog or the time budget. You then get the new screen, the reason and the "
    "actions that did not run: continue from there, do not redo steps that ran."
)

PERMISSION_NOTE = (
    "System permission pop-ups are answered automatically (Allow), unless the "
    'test asks to deny one: then pass deny_permissions=["location"] (the groups '
    "to refuse) to qa_mobile_test and only those are declined. If the test IS "
    "the permission prompt, pass grant_permissions=false and answer the pop-up "
    "yourself."
)

#: Per-turn pointer for packets billed every turn: one short line.
PACKET_POINTER = (
    "Plan past this screen (3-4 actions per screen, up to "
    + str(MAX_MODEL_ACTIONS)
    + " per submit; tap_text_if_present for maybe-screens; end with an assert)."
)
