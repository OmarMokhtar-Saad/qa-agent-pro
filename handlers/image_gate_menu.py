"""Jira image gate: the BEAT 1 markdown menu, its cause wording and its warnings."""

from __future__ import annotations

from handlers.image_gate_labels import _IMAGE_GATE_LINE, _MARK_UNREADABLE_ANSWER
from handlers.image_gate_plan import _gated_not_unsupported, _rejected_plan_note


# BEAT 1 tier-3 fallback: the menu as an instruction to the HOST assistant.
#
# Same shape as _tc_source_menu_markdown -- editors render a structured
# multiple-choice question reliably where an MCP elicitation dialog may be
# auto-dismissed. NEVER a dead end: every option names the exact re-call.
#
# 2026-08-09 (Batch 3, FIX 3, review H4): *elicit_status* is the
# "<enum>/<text>" outcome from elicit_source_plan_status, used ONLY to word
# the CAUSE of this fallback correctly. This menu is relayed whenever no plan
# resolved, which includes a call arriving with no elicitation channel at
# all and the tester DECLINING the dialog -- so asserting "your client has
# no elicitation support" unconditionally would be an over-claim, in the one
# batch dedicated to removing over-claims. 2026-08-14: the "disabled" label
# no longer means a server setting. QA_MCP_ELICIT_ENABLED was DELETED on
# 2026-08-13 and hardcoded ON, so the elicit seam is gone (WD) and
# the only surviving cause is both callbacks being absent.
# Defaults to "" (cause unknown), which words it as a plain statement of fact
# with no cause attributed.
#
# 2026-09-01: the "unavailable" label is NOT proof of a client limitation.
# A client gated by ``elicit_client_gated()`` arrives with both callbacks
# None and is labelled ``unavailable/unavailable`` ON PURPOSE, so the
# mcp_image_gate_beat1 audit row stays byte-identical (see
# elicit_source_plan_status). Translating that into "your MCP client does
# not support elicitation dialogs" is false for the very client that earns
# the gate -- Cursor SHOWED the dialog and dismissed it -- and it is the
# over-claim this function was written to remove. The gate is asked
# directly rather than inferred from a label that never carried capability.
# Never raises.
def _image_gate_menu_markdown(elicit_status: str = "", rejected_plan: str = "") -> str:
    """Return the BEAT 1 tier-3 fallback: the menu as an instruction to the host assistant."""
    return (
        "## The ticket is staged: how do I get its screens?\n\n"
        + _rejected_plan_note(rejected_plan)
        + "> ℹ️ "
        + _IMAGE_GATE_LINE
        + "\n\n"
        + _IMAGE_GATE_OPTIONS
        + _image_gate_warnings(_image_gate_cause(elicit_status))
    )


_CAUSE_DEFAULT = "could not be shown to you inline"
_CAUSE_ANSWERED = (
    f"was answered, but I {_MARK_UNREADABLE_ANSWER} as one of the five plans below"
)
_CAUSE_NO_CHANNEL = (
    "was not shown inline because this call arrived with no elicitation "
    "channel (your client offered none for this call)"
)
_CAUSE_DECLINED = "was shown inline and dismissed"
_CAUSE_GATED = "was not shown inline because pop-up dialogs aren't available here"
_CAUSE_UNSUPPORTED = (
    "could not be shown inline because your MCP client does not support "
    "elicitation dialogs"
)


# Wording of WHY the BEAT 1 dialog fell back to the text menu (see
# _image_gate_menu_markdown for why each label is worded as it is).
#
# 2026-08-31: the "/chosen" arm. The tester ANSWERED -- the text tier reported
# CHOSEN and the caller still got "" back, which can only mean the reply did not
# normalize to one of the five plans. Reporting a client limitation here is an
# over-claim (the dialog tiers are not why we are in the fallback) and it hides
# the one thing the tester can act on: say it again, in the server's own words.
# Checked FIRST because such a status also carries the enum tier's
# "unavailable"/"declined" label, which used to win.
def _image_gate_cause(elicit_status: str) -> str:
    """Return the wording of why the beat 1 dialog fell back to the text menu."""
    if elicit_status.endswith("/chosen"):
        return _CAUSE_ANSWERED
    if elicit_status.startswith("disabled"):
        return _CAUSE_NO_CHANNEL
    if "declined" in elicit_status:
        return _CAUSE_DECLINED
    if "unavailable" in elicit_status:
        return _CAUSE_GATED if _gated_not_unsupported() else _CAUSE_UNSUPPORTED
    return _CAUSE_DEFAULT


_IMAGE_GATE_OPTIONS = (
    "Present EXACTLY these five options to the user as a multiple-choice "
    "question (use your ask-user/questions UI, not prose), then call the "
    "SAME tool again with the SAME `feature_or_url` plus `source_plan` set "
    "to the value in brackets. The ticket is already staged: do NOT fetch it again, and do not "
    "invent other options. Do NOT ask the tester additional questions about "
    "which device or how many screens — pass what they gave you and let the "
    "server default the rest.\n\n"
    "1. **Ticket text only** [`jira`] -- no screens matter here. If the "
    "fetched ticket then turns out to contain screens, I will name them and "
    "ask ONCE more; send `image_gate_ack=true` alongside `source_plan` to "
    "skip that second ask.\n"
    "2. **I'll attach the screenshots to this chat** [`jira_attach`] -- "
    "attach them, then also pass `attached_image_count=<how many>`\n"
    "3. **Capture them from a connected device** [`jira_device`] -- call "
    "`qa_capture_screens` first, then pass its `capture_ids`\n"
    "4. **Both** [`jira_both`] -- chat attachments AND captured screens\n"
    "5. **Device screens only** [`device`] -- ignore the ticket text\n\n"
)


def _image_gate_warnings(cause: str) -> str:
    """The round-trip notice and the never-guess warning of the BEAT 1 menu."""
    return (
        "> \u23f1\ufe0f  **This reply cost a round trip.** My question "
        f"{cause}, so I had to hand it to you as text. NOTHING has been "
        "prepared -- no generation -- so nothing was wasted except "
        "this turn. To avoid it next time, ask the user where a Jira ticket's "
        "screens come from right after you stage the ticket, and pass "
        "`source_plan` on the `qa_prepare_test_cases` call that carries the "
        "`stage_token`.\n\n"
        "> \u26d4 **Only pass `source_plan` if the USER answered -- never guess "
        "it, and never send `image_gate_ack=true` unless the user explicitly "
        "said the screens do not matter.** Guessing `source_plan='jira'` skips "
        "this ask, and pairing it with `image_gate_ack=true` ALSO skips the "
        "second, informed ask that NAMES the screens the fetched ticket really "
        "has -- that is a quieter gate, not a cheaper one, and the cases get "
        "written from ticket text that may not contain the requirements.\n\n"
        "Many images are fine: attach as many as the user has, and "
        "`qa_capture_screens` captures screen after screen."
    )
