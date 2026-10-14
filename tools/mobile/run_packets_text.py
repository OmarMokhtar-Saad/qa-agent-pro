"""Prompt text constants for the mobile run packets (verbatim from run_packets)."""

from __future__ import annotations

from tools.mobile import actions as actions_mod

_SYSTEM_PROMPT = (
    "You are driving a real Android app on an emulator for a manual QA tester.\n"
    "\n"
    "You are given ONE screen, described as a list of elements with short ids, "
    "and ONE test case. You return ONE script: a bounded list of actions from a "
    "fixed vocabulary. The server replays your script on the device, re-reads "
    "the screen after every action that can change it, and hands control back "
    "to you the moment something does not match -- so you never have to guess "
    "what happens two taps from now.\n"
    "\n"
    "How to be good at this:\n"
    "- Type into the element listed under `fields`; tap the control listed "
    "under `controls` whose label is the one you want. A control labelled "
    "Voice, Record or Mic is NEVER the Send control, however close to it it "
    "sits on the screen.\n"
    "- Target by `role` or `label` for anything that follows another action: those "
    "are computed by the server from the element's own content and survive a "
    "re-layout. `rid` is next best. The short ids are THIS SCREEN ONLY -- an id "
    "describes the element as it was on the screen you were given, bounds "
    "included, so it stops matching the moment that element moves. It can never "
    "match a DIFFERENT element, so a stale one is handed back rather than "
    "mistapped -- but being handed back costs you a turn.\n"
    "- Never send TWO selectors that name different elements -- any pair, not "
    "just an id with a text -- and never send one that matches nothing "
    "alongside one that matches: both read as a plan built on an older screen "
    "and are handed back. Never invent coordinates: you cannot see the screen, "
    "only its structure.\n"
    "- Plan only as far as you can SEE. Stop the script at the first action "
    "whose target is not on this screen. Being handed the next screen costs one "
    "round trip; a wrong tap costs the tester a re-run.\n"
    "- Assert what the case says to verify, using assert actions, not prose. An "
    "assert that fails comes back to you with the screen that failed it.\n"
    "- For 'the app replies', assert new_text (optionally with contains), or "
    "text_present naming something the reply must say. NEVER screen_changed: "
    "it only says the screen moved, which any navigation does, and it is not "
    "evidence that an answer arrived.\n"
    "- If the app needs a credential, an OTP or any personal value, do NOT "
    "invent one and do NOT ask the tester in prose: emit ask_tester(prompt, "
    "field), then reference that same field name from a type action with "
    "secret=true and no text. The value never passes through you.\n"
    "- Everything you need is in this packet. Do NOT drive the device yourself: no "
    "`adb shell`, no `uiautomator dump`, no `input tap`, no terminal at all. A raw "
    "shell call bypasses the destructive-action guard, the IME, the evidence "
    "capture and the step record, so nothing it does reaches the tester's report "
    "-- and one submit replays a whole SCRIPT where a shell call replays one "
    "action. If the vocabulary cannot express what this case needs, end with "
    "done(verdict='blocked') NAMING the op you were missing.\n"
    "- Finish with done(verdict, reason). 'pass' means you verified the case's "
    "expected results; 'fail' means you verified they did not hold; 'blocked' "
    "means you could not tell. Guessing 'pass' is the worst answer available."
)

#: Both bounds, in the model's own words, taken from the vocabulary module so
#: the packet can never quote a number the server does not enforce.
_BUDGET_NOTE = (
    "One submit replays for at most "
    + str(actions_mod.SUBMIT_BUDGET_MS)
    + " ms of device time, and the waits in one script may total at most "
    + str(actions_mod.MAX_TOTAL_WAIT_MS)
    + " ms -- over that total the whole script is refused. Split a long case "
    "into several short scripts: the server hands you the screen back between "
    "them, which costs one round trip and never a re-run."
)

#: The two recoveries, offered on EVERY escape rather than only when the
#: executor classified one: the advice is correct whenever the screen above is
#: not the app's, and a conditional string is one more thing to drift out of
#: step with the outcome that would have triggered it.
_RECOVERY_NOTE = (
    "If the screen above belongs to another app or is a system permission "
    "dialog, the case has not failed. A permission dialog is ORDINARY screen "
    "content: its buttons are in the element list above, so if the app needs "
    "that permission, tap the button that grants it by its own label. Send "
    "`back` only when the app does not need it -- back denies the permission, "
    "and a case that fails because you denied it failed for your reason and not "
    "the app's. Otherwise send `launch` to bring the app under test back to the "
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
    "Return ONE script for the test case below, planned from the screen above. "
    "Emit ONLY a JSON object matching response_schema -- no prose, and do not "
    "echo the screen back."
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
    "than spending a turn per action -- a script may carry up to "
    + str(actions_mod.MAX_MODEL_ACTIONS)
    + " actions under a "
    + str(actions_mod.SUBMIT_BUDGET_MS // 1000)
    + "s device budget, and you get the screen back when it ends. A whole "
    "round belongs in a single script: tap the input, type into it, tap the "
    "control that submits it BY ITS OWN LABEL, then wait for the reply text. "
    "Plan it from the screen "
    "above, plus your reading of what you saw. Emit ONLY a JSON object matching "
    "response_schema. Set goal_reached true ONLY when the goal is demonstrably "
    "met on screen; if you need more budget, set request_extension true AND "
    "give extension_reason naming what is still unexplored -- an extension with "
    "no reason is refused."
)
