"""Jira image gate: plan names, menu labels, stable markers and the always-shown line."""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Jira image gate (QA_IMAGE_GATE_ENABLED) -- Approach C, TWO BEATS
#
# BEAT 1 fires AFTER the ticket is staged, from handle_prepare_test_cases, for a Jira
# source with no source_plan: it discloses that this server cannot read images
# out of Jira and collects a plan (attach / capture / both / text-only). BEAT 2
# fires on the SECOND call, once the ticket is in hand, ONLY when the ticket
# revealed images that nothing supplied -- and it NAMES the screens using the
# ticket's own labels/filenames.
#
# Why a gate at all: tools/jira_mcp makes no outbound HTTP request by hard rule
# and the Atlassian MCP server returns attachment METADATA only, so the existing
# disclosure could only ever be POST-HOC -- appended to a payload the tester's
# chat model had already been told to generate from. Moving it to the FRONT is
# the whole point.
#
# REACHABILITY: both beats live in handle_prepare_test_cases, and
# handle_generate_test_cases RE-ROUTES into it whenever the generation mode
# resolves to host (hardcoded true since 2026-08-01), so the gate fires on
# qa_generate_test_cases too. That tool therefore forwards the same four gate
# arguments -- otherwise the only way to answer the question would be to call a
# DIFFERENT tool, which a client that auto-cancels dialogs cannot recover from.
#
# Every ask uses the three-tier fallback the rest of this module uses (enum
# dialog -> free text -> markdown the host relays), because Cursor 3.12
# auto-cancels enum dialogs: a gate that dead-ends is worse than no gate.
# --------------------------------------------------------------------------- #

_IMAGE_SOURCE_PLANS = ("jira", "jira_attach", "jira_device", "jira_both", "device")

# Label -> plan, mirroring _TC_SOURCE_LABELS' shape so both menus behave alike.
_IMAGE_SOURCE_LABELS = {
    "Ticket text only -- no screens matter here": "jira",
    "I will attach the screenshots to this chat": "jira_attach",
    "Capture the screens from a connected device": "jira_device",
    "Both -- attach some AND capture from a device": "jira_both",
    "Device screens only -- ignore the ticket text": "device",
}

# STABLE MARKERS (2026-08-31). Two release-gate checks used to assert
# on raw sentences from the strings below. That makes a copy edit
# indistinguishable from a regression at the gate -- exactly the false
# alarm the release-verification work was undertaken to remove. Naming
# the fragments does not freeze the wording: it freezes the PHRASE the
# checker keys on, which a rewrite may carry unchanged. Dropping one is
# then a deliberate edit to a named symbol, not an accident.
_MARK_UNREADABLE_ANSWER = "could not read the answer"
_MARK_UNNAMED_REMAINDER = "does not name the remaining"

# The line that is ALWAYS shown -- in both beats and in the markdown fallback.
_IMAGE_GATE_LINE = (
    "**I cannot read images out of Jira -- only text.** Jira is read through "
    "your own Atlassian MCP connection, which returns attachment metadata "
    "(filenames) but never the image bytes. If this ticket's requirements live "
    "in mockups or screenshots, the cases will be written from the ticket TEXT "
    "alone unless you give me the images another way."
)

# P0-3: `image_gate_ack=true` is the AGENT's say-so that the tester agreed to
# skip the screens. Beat 2 asks the tester directly; only this skip label,
# CHOSEN by them, clears it. There is no agent-side override.
_IMAGE_GATE_SKIP_LABEL = "Skip the screens -- generate from the ticket text only"
_IMAGE_GATE_ATTACH_LABEL = "I will attach or capture the screens first"


_IMAGE_PLAN_ALIASES = {
    "text": "jira",
    "text_only": "jira",
    "ticket": "jira",
    "ticket_only": "jira",
    "ticket_text": "jira",
    "ticket_text_only": "jira",
    "no_screens": "jira",
    "jira_only": "jira",
    "attach": "jira_attach",
    "attachment": "jira_attach",
    "attachments": "jira_attach",
    "screenshot": "jira_attach",
    "screenshots": "jira_attach",
    "chat": "jira_attach",
    "capture": "jira_device",
    "mobile": "jira_device",
    "jira_mobile": "jira_device",
    "device_only": "device",
    "device_screens": "device",
    "device_screens_only": "device",
    "both": "jira_both",
    "attach_capture": "jira_both",
}

# 2026-08-31: the MENU ORDINALS, in the order _image_gate_menu_markdown prints
# them. A tester answering the tier-2 free-text prompt types "1", and a host
# relaying the markdown menu may echo the number the tester picked rather than
# the bracketed token. Both used to normalize to "" and cost another round trip.
_IMAGE_PLAN_ORDINALS = {
    "1": "jira",
    "2": "jira_attach",
    "3": "jira_device",
    "4": "jira_both",
    "5": "device",
}
