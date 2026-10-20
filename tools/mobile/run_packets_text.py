"""Prompt text constants for the mobile run packets (verbatim from run_packets)."""

from __future__ import annotations

from tools.mobile import actions as actions_mod

#: Cap on `_SYSTEM_PROMPT` + `_CASE_INSTRUCTION` together; the long form of the
#: rules lives in `tool_briefs.MOBILE_BRIEF` (sent once per chat).
RULES_MAX_CHARS = 1500

#: The bulk-action rule, said in the system prompt and the case instruction.
_BULK_RULE = (
    "Send every action you can do on THIS screen in one step (fill all "
    "fields, tick, then tap Next). Do not guess the next screen."
)

_SYSTEM_PROMPT = (
    "You drive a real Android app on an emulator for a QA tester. "
    "You get ONE screen (numbered element lines) "
    "and ONE test case; you return ONE script of at most "
    + str(actions_mod.MAX_MODEL_ACTIONS)
    + " actions from the fixed vocabulary. The server replays it, "
    "and hands control back on a screen mismatch.\n"
    "- " + _BULK_RULE + " Plan only as far as you can SEE.\n"
    '- Name an element by its line number: {"op":"tap","id":"#3"} '
    "refers to line #3 of the latest screen list. Numbers are valid only for "
    "the latest screen; after a new screen use its numbers. "
    "If the list ends with '\u2026 N more below (scroll)', scroll before "
    "concluding an element is absent. Never invent coordinates.\n"
    "- A tap already waits for the screen to settle. A screen that may not "
    "show: tap_text_if_present. End with an assert that proves the goal, then "
    "done(verdict, reason).\n"
    "- Credentials or OTP: do NOT invent one; emit ask_tester(prompt, field), "
    "then type it with secret=true.\n"
    "- Never `adb shell`, `uiautomator dump` or `input tap` (skips the destructive-action guard "
    "and evidence capture). If no op fits "
    "a step, end with done(verdict='blocked') naming the op.\n"
    "- During a run call ONLY qa_submit_mobile_step."
)

#: Both bounds, in the model's own words, taken from the vocabulary module so
#: the packet can never quote a number the server does not enforce.
_BUDGET_NOTE = (
    "One submit replays for at most "
    + str(actions_mod.SUBMIT_BUDGET_MS)
    + " ms of device time, and the waits in one script may total at most "
    + str(actions_mod.MAX_TOTAL_WAIT_MS)
    + " ms -- over that total the whole script is refused. Split a long case "
    "only when it exceeds this: the server hands you the screen back between "
    "scripts, which costs one round trip and never a re-run."
)

#: The two recoveries, offered on EVERY escape rather than only when the
#: executor classified one: the advice is correct whenever the screen above is
#: not the app's, and a conditional string is one more thing to drift out of
#: step with the outcome that would have triggered it.
_RECOVERY_NOTE = (
    "If the screen above belongs to another app or is a system permission "
    "dialog, the case has not failed. Permission pop-ups are normally "
    "answered for you (Allow), unless the test asked to deny it; one still on "
    "screen is ORDINARY content, if the app needs that permission, tap the button that grants it by its own label. "
    "Send `back` only when the app does not need it -- back denies the "
    "permission. Otherwise send `launch` to bring the app under test back to the "
    "front. Put that "
    "recovery FIRST IN THE SAME SCRIPT as the rest of your plan -- a script "
    "that reaches its end without done() ENDS the case, so a recovery sent on "
    "its own finishes the case instead of continuing it. "
)

#: Said on the packet BEFORE the last hand-back, not after it. A model that
#: learns the budget is spent only when the case is already `blocked` has been
#: told nothing it could act on.
_LAST_ESCAPE_NOTE = (
    "This is the LAST hand-back for this case: if it is not finished from "
    "here, it is recorded blocked and will not be handed out again in this "
    "run. Either finish it, or end it with done(verdict='blocked') saying "
    "what stopped it -- an honest blocked is worth more than a fourth attempt "
    "you do not get. "
)

_CASE_INSTRUCTION = (
    "Return ONE script for the test case below. "
    + _BULK_RULE
    + " Emit ONLY a JSON object matching response_schema -- no prose, and do "
    "not echo the screen back."
)

_ESCAPE_INSTRUCTION = (
    "Your previous script stopped part-way. The trace below shows what ran and "
    "why it stopped, and the screen above is the CURRENT one. Return a NEW "
    "script that continues the case from here -- do not repeat actions the "
    "trace records as already done. Emit ONLY a JSON object matching "
    "response_schema."
)

_EXPLORE_INSTRUCTION = (
    "This is one turn of a bounded exploratory session. The budget is TURNS, "
    "not actions, so put the WHOLE of your next intent in ONE script rather "
    "than spending a turn per action; plan past this screen, "
    "tap_text_if_present for screens that may not show -- a script may carry up to "
    + str(actions_mod.MAX_MODEL_ACTIONS)
    + " actions under a "
    + str(actions_mod.SUBMIT_BUDGET_MS // 1000)
    + "s device budget, and you get the screen back when it ends. A whole "
    "round belongs in a single script: tap the input, type into it, tap the "
    "control that submits it BY ITS OWN LABEL, then wait for the reply text. "
    "Plan it from the screen "
    "above, plus your reading of what you saw. Emit ONLY a JSON object matching "
    "response_schema. End each script with an assert action for what the turn "
    "proved, then call done(); a turn with no assert has no verdict. "
    "Inside the script JSON (never as tool arguments), set goal_reached true "
    "ONLY when the goal is demonstrably "
    "met on screen; if you need more budget, set request_extension true AND "
    "give extension_reason naming what is still unexplored -- an extension with "
    "no reason is refused."
)

if len(_SYSTEM_PROMPT) + len(_CASE_INSTRUCTION) > RULES_MAX_CHARS:
    raise ValueError("packet rules exceed RULES_MAX_CHARS; move text to MOBILE_BRIEF")
