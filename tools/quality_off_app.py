"""Part of the quality-check split (see tools/quality_checks.py): off app."""

from __future__ import annotations

import re

# --- First-step findability -------------------------------------------------
# The generator is ALREADY told this rule twice per category job:
# agents.test_scenario_agent._CATEGORY_RULES ("LOCATION MUST BE FINDABLE", in
# the system prompt) and _QUALITY_RULES_BODY (in the per-category instruction).
# The 2026-08-16 live run (suite 1ed83399b4b84831b79ead7936235989) still opened
# 23 of its 96 cases inside a field or on a toggle with no route to the screen,
# because 8 parallel category workers each decide this blind and NOTHING
# verified the result. This is that missing verifier.
#
# ADVISORY only: it never rejects a case and never triggers a regeneration, so
# it cannot inflate step counts -- a short case is correct by design under the
# atomicity rule.

# A first step that is not app navigation at all -- a terminal, a gateway, a
# backend call, another channel, or the login/logout boundary itself. These have
# NO app screen to name and must never be flagged: 14 of that run's 96 cases
# were legitimately here ("Attempt ATM withdrawal SAR 500.00 on debit 4521"), a
# fifth of a good suite.
#
# An entry whose noun COLLIDES with a live in-app control name is SCOPED to the
# off-app EVENT rather than the bare noun, because a bare noun silences the
# in-app CONTROL that shares its name -- the same mistake three separate reviews
# caught one keyword at a time:
#
#   bare "purchase"  silenced  "Toggle Online purchases OFF"      (TC-030/093)
#   bare "email"     silenced  "Enter ... into the 'Email' field"
#   bare "terminal"  silenced  "Enter 5000 in the terminal ID field"
#   bare "contactless" silenced "Toggle Contactless payments OFF"
#   bare "withdrawal"  silenced "Set the daily withdrawal limit to SAR 1,000"
#
# The last two are structural twins of the finding's own clear-14. A detector
# keyed to vocabulary rather than to findability is a coin flip on the next
# suite in the same domain, so the scoping is the rule here, not the exception.
#
# RESIDUAL, disclosed rather than closed (round 10): about ten of the
# server-side and human-channel entries below ARE still bare nouns -- "payment
# gateway", "acquirer", "deep link", "point of sale", "contact cent(re|er)",
# "bank branch", the "merchant terminal/site/..." compounds. A toggle NAMED
# after one is exempted: "Toggle Payment gateway alerts OFF on debit 4521." is
# silent while its structural twin "Toggle Online purchases OFF" is a clear-14
# flag. They stay bare because no live suite has yet shown the collision,
# scoping each needs a verb/amount pattern measured in both directions, and the
# error resolves toward SILENCE -- the cheap direction for an advisory check.
# Pinned as a known false negative in op 5, and named in the plan's "Known
# false negatives", so the residual is visible rather than claimed away.
_OFF_APP_FIRST_STEP = re.compile(
    # Card-present channels: the terminal itself, or an amount taken at one.
    # "ATM withdrawal" / "POS purchase" need the EVENT (an attempt verb or an
    # amount) because both are also the names of in-app toggles on a card-
    # controls screen -- this suite's own TC-034 is "Disable ATM withdrawals".
    r"\b(?:attempt|make|perform|complete|initiate)\s+(?:\w+\s+){0,2}"
    r"(?:atm withdrawals?|pos purchases?)\b"
    r"|\b(?:atm withdrawals?|pos purchases?)\s+(?:of\s+)?(?:[A-Z]{3}\s*)?\d"
    r"|\batm\s+(?:machine|screen)\b|\b(?:at|from)\s+(?:the\s+|an?\s+|test\s+)*atm\b"
    r"|\bpos\s+(?:terminal|machine|device)\b|\bpoint of sale\b"
    r"|\b(?:at|from)\s+(?:the\s+|a\s+)?pos\b"
    r"|\b(?:at|from)\s+(?:the\s+|a\s+|test\s+)*merchant\b"
    r"|\bmerchant\s+(?:terminal|site|website|checkout|portal)\b"
    r"|\b(?:attempt|make|perform|tap|complete)\s+(?:\w+\s+){0,2}contactless\b"
    r"|\bcontactless\s+(?:[A-Z]{3}\s*)?\d"
    # Server-side and integration boundaries.
    r"|\bapi\s+(?:call|request|endpoint|response|payload)\b|\bvia\s+(?:the\s+)?api\b"
    r"|\bdeep link\b|\bintercept(?:ing|ed)?\b"
    r"|\b(?:in|from|against|query|querying|check)\s+(?:the\s+)?(?:database|backend|back-end)\b"
    r"|\bbackend\s+(?:api|service|job|record|db)\b"
    r"|\bwebhook\s+(?:payload|call|delivery|event)\b|\bvia\s+(?:a\s+)?webhook\b"
    r"|\bcrm\s+(?:event|record|bus|system|sync)\b|\bin\s+(?:the\s+)?crm\b"
    r"|\b(?:via|through|at)\s+(?:the\s+)?payment processor\b"
    r"|\bpayment processor\s+(?:response|declines?|returns|rejects)\b"
    r"|\bpayment gateway\b|\bauth(?:orisation|orization)\s+gateway\b|\bacquirer\b"
    # Other human channels.
    r"|\bcontact cent(?:re|er)\b|\bcall cent(?:re|er)\b"
    r"|\bbank branch\b|\bbranch\s+(?:office|counter)\b|\bat\s+(?:the\s+|a\s+)?branch\b"
    r"|\bussd\b\s*\*?\d|\bdial\b.{0,20}\bussd\b|\*\d{2,}#"
    # A push notification is a place, but only when the case STARTS there --
    # "inspect push notification payload" is an in-app assertion about one.
    r"|^\s*(?:wait for|open|tap|check|receive)\s+(?:the\s+)?(?:\w+\s+){0,2}push notification\b"
    r"|\bfrom\s+(?:the\s+)?notification (?:shade|cent(?:re|er)|tray)\b"
    r"|\btransaction attempt\b",
    re.IGNORECASE,
)

# The login/logout boundary is a legitimate place to start and has no in-app
# screen to name -- but only as the ACTION. A bare keyword exempts "Tap the
# Login button", an in-app control. So: sentence-initial, or after an attempt
# verb. \blog ?in\b never matches "logged in", which is a precondition phrase.
_AUTH_BOUNDARY = re.compile(
    r"^\s*(?:attempt(?:ing)?\s+|perform\s+|complete\s+)?(?:a\s+)?"
    r"(?:log ?in|log ?out|sign ?in|sign ?out)\b"
    r"|\b(?:attempt|perform|complete)\s+(?:a\s+)?"
    r"(?:log ?in|log ?out|sign ?in|sign ?out)\b"
    r"|\bwithout logging in\b",
    re.IGNORECASE,
)

# A purchase/payment EVENT happens off-app; "Online purchases" is the NAME of an
# in-app toggle. Only the event form exempts -- measured: a bare "purchase"
# keyword wrongly exempted "Toggle Online purchases OFF on debit 4521", two of
# the clearest weak cases in that run. The amount form is currency-agnostic so
# it is not tuned to the one SAR suite it was measured on.
_OFF_APP_TRANSACTION = re.compile(
    r"\b(?:attempt|make|makes|complete|perform|initiate|process)\s+"
    r"(?:\w+\s+){0,3}(?:purchase|payment|transaction)\b"
    r"|\b(?:purchase|payment|transaction)\s+(?:of\s+)?(?:[A-Z]{3}\s*)?\d",
    re.IGNORECASE,
)

# An out-of-app MESSAGE the tester leaves the app to read. COMPOUND destinations
# only: a bare "email"/"sms"/"otp" keyword exempts "Read the OTP field", "Check
# the SMS notification toggle" and every 'Email' field on a login suite, which
# are in-app controls named after a channel -- precisely the class this detector
# must catch.
_OFF_APP_MESSAGE = re.compile(
    r"\b(?:inbox|mailbox|email client|"
    r"(?:verification|confirmation|activation|reset) email|"
    r"otp (?:sms|message|email|code)|sms (?:message|code)|text message)\b",
    re.IGNORECASE,
)
