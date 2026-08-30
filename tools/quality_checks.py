"""Cheap heuristic quality gate for generated test cases.

Flags vague step actions (e.g. "enter any value") and placeholder test_data
(e.g. "anything") so drift can't silently reach the exported files. This is a
bounded, regex-based heuristic — not a semantic check — kept intentionally
cheap so it can run on every category's output without an extra LLM call.
Never raises to callers.

Deliberately does NOT touch technical/security step *content* — it only flags
vague phrasing versus a literal value; a step that opens DevTools, inspects
headers, or uses a SQL/XSS payload is fine as long as the payload/value/field
is spelled out.
"""

from __future__ import annotations

import logging
import re

from tools.models import TestCase

logger = logging.getLogger(__name__)

# Vague/non-concrete step action phrasing — the step names an action but not
# the literal value/payload used, e.g. "enter a classic SQL injection string"
# instead of "Enter ' OR '1'='1 into the 'Username' field".
_VAGUE_ACTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"\benter\s+(a|an|any|some)\s+valid\b", re.IGNORECASE),
    re.compile(r"\benter\s+any\s+value\b", re.IGNORECASE),
    re.compile(r"\benter\s+some\s+value\b", re.IGNORECASE),
    re.compile(r"\benter\s+a\s+random\b", re.IGNORECASE),
    re.compile(r"\buse\s+a\s+random\b", re.IGNORECASE),
    re.compile(r"\b(a|an)\s+classic\s+sql\s+injection\s+string\b", re.IGNORECASE),
    re.compile(r"\ba\s+sql\s+injection\s+string\b(?!\s*[:(].{0,60})", re.IGNORECASE),
    re.compile(r"\bany\s+(invalid|incorrect)\s+value\b", re.IGNORECASE),
    re.compile(r"\bsome\s+(invalid|incorrect)\s+data\b", re.IGNORECASE),
]

# Vague expected_result phrasing — asserts a qualifier ("appropriate error
# message", "works correctly") instead of the concrete observable outcome the
# tester should verify (the exact on-screen message, field/button state, or
# resulting page). These qualifiers are almost never followed by the actual
# text, so flagging them has a low false-positive rate.
_VAGUE_EXPECTED_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"\b(appropriate|proper|suitable|relevant)\s+(error\s+)?(message|response|validation|feedback)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(behaves?|works?|functions?)\s+(correctly|as\s+expected)\b", re.IGNORECASE
    ),
    re.compile(
        r"\bhandled\s+(gracefully|properly|appropriately|correctly)\b", re.IGNORECASE
    ),
    re.compile(r"\bvalidat(?:es|ed|ion)\s+properly\b", re.IGNORECASE),
]

# Placeholder test_data values — a field name with no concrete example value.
_PLACEHOLDER_DATA_VALUES = {
    "anything",
    "any value",
    "any password",
    "any data",
    "some value",
    "valid data",
    "n/a",
    "na",
    "tbd",
    "todo",
}


# A concrete value present in the same text — a quoted literal, a 4+ digit run
# (phone/id/amount), or an email — means the step/result is actionable even if it
# also contains a "valid X" / "appropriate message" qualifier, so it should NOT be
# flagged as vague (e.g. "Enter a valid mobile number '01111222333'").
_CONCRETE_VALUE_RE = re.compile(r"""'[^']{2,}'|"[^"]{2,}"|\d{4,}|@\w+\.\w+""")


def _has_concrete_value(text: str) -> bool:
    try:
        return bool(_CONCRETE_VALUE_RE.search(text))
    except Exception:
        return False


def _is_vague_action(action: str) -> bool:
    try:
        if not any(p.search(action) for p in _VAGUE_ACTION_PATTERNS):
            return False
        return not _has_concrete_value(action)
    except Exception:
        return False


def _is_vague_expected(expected: str | None) -> bool:
    if not expected:
        return False
    try:
        if not any(p.search(expected) for p in _VAGUE_EXPECTED_PATTERNS):
            return False
        return not _has_concrete_value(expected)
    except Exception:
        return False


def _is_placeholder_data(test_data: str | None) -> bool:
    if not test_data:
        return False
    try:
        normalized = test_data.strip().lower()
        if normalized in _PLACEHOLDER_DATA_VALUES:
            return True
        # "field: <placeholder>" form, e.g. "password: anything"
        for part in re.split(r"[,;\n]", normalized):
            value = part.split(":", 1)[-1].strip()
            if value in _PLACEHOLDER_DATA_VALUES:
                return True
        return False
    except Exception:
        return False


def find_vague_steps(cases: list[TestCase]) -> list[tuple[str, int, str]]:
    """Return (tc_id, step_number, action) for every step with vague phrasing. Never raises."""
    try:
        return [
            (tc.tc_id, step.step_number, step.action)
            for tc in cases
            for step in tc.steps
            if _is_vague_action(step.action)
        ]
    except Exception:
        logger.exception("find_vague_steps failed — returning empty list")
        return []


def find_vague_expected(cases: list[TestCase]) -> list[tuple[str, int, str]]:
    """Return (tc_id, step_number, expected_result) for every step whose expected
    result uses vague qualifier phrasing instead of a concrete outcome. Never raises."""
    try:
        return [
            (tc.tc_id, step.step_number, step.expected_result)
            for tc in cases
            for step in tc.steps
            if _is_vague_expected(step.expected_result)
        ]
    except Exception:
        logger.exception("find_vague_expected failed — returning empty list")
        return []


def find_placeholder_data(cases: list[TestCase]) -> list[tuple[str, int, str]]:
    """Return (tc_id, step_number, test_data) for every step with placeholder test data. Never raises."""
    try:
        return [
            (tc.tc_id, step.step_number, step.test_data)
            for tc in cases
            for step in tc.steps
            if _is_placeholder_data(step.test_data)
        ]
    except Exception:
        logger.exception("find_placeholder_data failed — returning empty list")
        return []


# Suite-level shortfall thresholds. A single case with no preconditions and an
# empty test_data plan is normal (a pure navigation check needs neither), but a
# whole suite of them means the generator put every value in prose inside the
# step text, leaving the exported Preconditions and Test Data columns blank.
# Module constants, deliberately NOT settings flags (CLAUDE.md flag policy):
# this is an advisory warning with no per-install right answer to tune.
EMPTY_DATA_WARN_RATIO = 0.8
# EMPTY_DATA_MIN_CASES is ALSO the suite-size floor for data_notes_section's
# missing-plan disclosure (F01, 2026-08-16). Below it a fixture is too small for
# "N of M cases declare a data plan" to mean anything, and reusing this constant
# rather than inventing a second one keeps every 1-4 case unit fixture
# byte-identical. A real run is always >= 8 cases (8 categories), so the floor
# never suppresses the disclosure a tester would actually see.
EMPTY_DATA_MIN_CASES = 5

# Cap on the per-case enumeration in data_notes_section. One line per case is a
# flood on a real run — 96 cases meant 96 lines, which pushed the finalize reply
# past the 4000-char cap in tools/mcp_handlers.shape_generation_result and
# truncated everything after this section (live repro 2026-08-15). Same failure
# shape as the checklist NOT-COVERED flood suppressed in tools/rtm.py. The full
# plan still ships in the Test Data column of the exported file. Module
# constant, deliberately NOT a settings flag (CLAUDE.md flag policy): a display
# cap has no per-install right answer to tune.
DATA_NOTES_MAX_CASES = 10

# D1 (SHYJ-5138, 2026-08-21): the gap line NAMES the affected cases now --
# but only while they are a MINORITY of the suite. F01 (2026-08-16) made the
# line counts-only for reply-budget reasons, and the count duly fired on the
# live run ("49 of 64 ... 15 declare none"); what it could not do is tell the
# tester to open TC-006 (catalog API load), TC-024 (HTTP 503) or TC-041 (BR01
# alphabetical order). A fifth prompt clause was rejected rather than tried:
# memory e02-null-result-2026-08-20 measured prose as a CONFIRMED WEAK LEVER
# on this generator, and this finding already recurred once (19 of 96, memory
# live-validation-2026-08-16) with all four existing clauses live.
#
# THE RATIO GATE IS LOAD-BEARING, AND MEASURED. Above EMPTY_DATA_WARN_RATIO
# the ids are suppressed and this line is byte-identical to its pre-SHYJ-5138
# form, for two independent reasons: (1) the suite-level advisory in
# quality_warning_section already owns that message ("91% of 96 case(s) have
# no test data plan"), so 12 of 87 ids is noise on top of it; (2) that shape
# has the LEAST reply headroom of any -- a suite where almost nothing
# declares a plan has almost no enumeration left to shrink in payment. A
# first draft of this change named ids unconditionally and measured the
# 87-of-96 fixture in tests/test_finalize_reply_cap.py at 4025 chars against
# mcp_handlers._SUMMARY_CAP = 4000 -- over by 25, because dropping
# enumeration rows there CREATED an "... and N more" overflow line that had
# been absent. See `limit` below for the second half of that guard.
# Measured after the gate: 0/96 and 87/96 byte-identical, 19/96 -47 chars.
# Twelve ids covers the live 15-case run with a "(+3 more)" tail, and the
# per-id width cap bounds the whole line at ~190 chars even for a malformed
# tc_id. Module constants, deliberately NOT settings flags (CLAUDE.md flag
# policy): a display cap has no per-install right answer to tune.
DATA_GAP_MAX_IDS = 12
DATA_GAP_MAX_ID_CHARS = 12
DATA_GAP_ROW_COST = 4


def find_empty_data_cases(cases: list[TestCase]) -> list[str]:
    """Return the tc_id of every case carrying BOTH no preconditions and an empty
    case-level test_data plan. Never raises."""
    try:
        return [
            tc.tc_id
            for tc in cases
            if not (getattr(tc, "preconditions", None) or "").strip()
            and not (getattr(tc, "test_data", None) or [])
        ]
    except Exception:
        logger.exception("find_empty_data_cases failed — returning empty list")
        return []


def find_empty_test_data_cases(cases: list[TestCase]) -> list[str]:
    """Return the tc_id of every case whose case-level test_data plan is empty,
    measured INDEPENDENTLY of preconditions.

    A SPLIT from find_empty_data_cases above rather than a redefinition of it:
    that function's BOTH-empty meaning is pinned by test and still supplies the
    detail count in quality_warning_section. The conjunction was the bug --
    live run 2026-08-21, suite 817a09c8: 58 of 64 cases shipped an empty
    test_data plan (90.6%, well past EMPTY_DATA_WARN_RATIO) while every one of
    them carried good preconditions, so the BOTH-empty count was 0 and the
    advisory could not fire. The exported Test Data column was blank on 58 rows.

    BOTH-empty is a strict subset of this, so the gate built on it is a strict
    superset of the old one: no warning that used to fire stops firing.

    Never raises."""
    try:
        return [tc.tc_id for tc in cases if not (getattr(tc, "test_data", None) or [])]
    except Exception:
        logger.exception("find_empty_test_data_cases failed — returning empty list")
        return []


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

# A named place the tester can be standing. Deliberately excludes the generic
# container words settings/section/view/panel: "Change settings on debit 4521"
# names no route, and treating it as a location is a silent false negative.
#
# That exclusion is a POLICY, not a property of this one regex, and
# _GENERIC_CONTAINER_NOUNS below holds exactly those four words so the head-noun
# path enforces it too. Round 5 caught them disagreeing on section/view/panel;
# round 6 caught "settings" still disagreeing after that fix. Change one of the
# two and change the other.
#
# Matching this regex is NOT sufficient on its own -- see _names_a_screen, which
# discards an occurrence that is modifying a control ("the menu ICON"). Round 6
# found that missing, which made this bare scan a fourth place predicate
# short-circuiting ahead of the head-noun test.
_SCREEN_NOUN = re.compile(
    r"\b(screen|page|tab\b|home\b|menu|dashboard|controls|drawer|sheet|dialog"
    r"|modal|popup|navigation bar|nav bar|toolbar|sidebar|header|footer)\b",
    re.IGNORECASE,
)
# "screen reader" is an accessibility tool, not a location.
_SCREEN_READER = re.compile(r"\bscreen\s+readers?\b", re.IGNORECASE)

# A locative phrase -- "in the cards list", "on debit 4521", "in the account
# number field" -- and what decides whether it names a PLACE is its HEAD NOUN,
# the last word before the phrase ends.
#
#   "in the cards list, tap ..."         head = list    -> a place
#   "in the account number field and ..." head = field   -> a control
#   "in per-transaction limit and ..."    head = limit   -> a value
#   "on debit 4521."                      head = 4521    -> not a noun at all
#   "on iPhone, change ..."               head = iphone  -> a device
#
# This replaces two earlier attempts that both failed the same way. Requiring
# the place to be CAPITALISED missed ordinary sentence-case writing; adding an
# allow-list of place nouns leaked, because a wildcard qualifier let "in the
# ACCOUNT number field" match on "account" and never reach "field". Head-noun is
# a rule rather than a list, which is what the third review round asked for: it
# needs no entry for "notification tray", "transaction history" or "payments
# hub", and it keeps every clear-14 field-opener weak.
_CONTROL_NOUNS = frozenset(
    """
    field fields box textbox input toggle switch checkbox radio dropdown
    picker slider button icon link label option setting settings counter
    limit cap amount value values threshold quantity price total count
    number code name id
    """.split()
)

# A device or browser says WHICH handset, not which screen.
_DEVICE_NOUNS = frozenset(
    """
    iphone ipad android ios device devices phone tablet handset emulator
    simulator desktop mobile web browser chrome safari firefox edge windows mac
    """.split()
)

# ... and a duration or a count says WHEN or HOW OFTEN, not where. "within 60
# seconds" is a locative preposition over a time unit, and live TC-059 ("Rapidly
# toggle freeze ON/OFF 20 times within 60 seconds on debit 4521") anchored on it
# the moment "on" joined _NP_BREAK and stopped shortening the phrase past it.
# Same shape as the device rule: a head noun from the wrong category is not a
# place.
_MEASURE_NOUNS = frozenset(
    """
    second seconds minute minutes hour hours day days week weeks month months
    year years time times attempt attempts try tries ms sec secs min mins
    """.split()
)

# The generic containers _SCREEN_NOUN deliberately excludes, enforced here too so
# the two paths cannot drift apart again. A container is a place only when the
# phrase NAMES it: "In the Spending Controls section" locates the tester, "In the
# monthly cap section" does not, because there is still no screen to open.
#
# This is not a new rule -- it is _NAV_TO_NAMED's existing "the destination must
# be NAMED (quoted or Capitalised)" test, applied to the constructions that were
# missing it.
#
# "settings" IS a member, and revision 6 wrongly claimed it needed no entry
# because _CONTROL_NOUNS "reaches the same answer". Round 6 measured the
# opposite: "In the Settings panel" anchored, because there Settings is the
# QUALIFIER and the naming rule accepted it, while "Navigate to Settings" was
# weak. Two verdicts for one word, which is the class this whole revision is
# about. It stays in _CONTROL_NOUNS as well, and that is not a contradiction --
# the two sets answer different questions. _GENERIC_CONTAINER_NOUNS governs the
# word in HEAD position ("in the notification settings"), _CONTROL_NOUNS governs
# it in MODIFIER position ("the merchant site settings"). _head_is_a_place tests
# the generic set first, so the head reading wins where both apply.
#
# The four words here are now exactly the four _SCREEN_NOUN's comment excludes.
# That identity is the point: if you add a word to one, add it to the other.
_GENERIC_CONTAINER_NOUNS = frozenset(
    "section sections view views panel panels setting settings".split()
)

# NAMED means quoted, or carrying a Capitalised token. This is a CAPITALISATION
# HEURISTIC standing in for a policy test, and round 6 was right to say so:
# "In the Monthly cap section" anchors and "in the monthly cap section" does not,
# though a tester can find neither or both equally. It also accepts a currency
# code ("In the SAR section") and rejects a genuine lowercase name ("in the card
# limits section").
#
# It is kept, disclosed and pinned in both directions rather than tuned, for two
# reasons. Capitalisation is the only signal free text actually carries for "this
# is a proper name" -- requiring quotes would reject "the Payments panel", and
# requiring two capitalised tokens would reject "the Cards view", both of which
# are real. And the error is BOUNDED to this one path: it can only affect a
# phrase whose head is one of the four container words above.
_NAMED_QUALIFIER = re.compile(r"'[^']+'|\"[^\"]+\"|\b[A-Z][\w-]*")

# Every noun category that is not a place, in one name. _head_is_a_place tests
# it, and _PHRASE_END subtracts it from the boundary verbs -- a word this
# detector recognises as a NOUN must not also end a noun phrase as a verb.
_NON_PLACE_HEADS = _CONTROL_NOUNS | _DEVICE_NOUNS | _MEASURE_NOUNS

# THE ONE DEFINITION OF WHERE A NOUN PHRASE ENDS. Two places need it -- the
# lookahead that bounds the three place patterns, and the compound test in
# _CONTROL_SUFFIX -- and round 6 showed what happens when they each carry their
# own: they disagreed about "of" and "to", so "terms OF service checkbox" was one
# noun phrase to one of them and two to the other.
#
# _NP_BREAK deliberately does NOT contain "of" or "to": they are the two function
# words that build compound nouns ("terms of service", "point of sale", "time to
# live"). Everything else here opens a new phrase.
#
# The SUBORDINATORS on the last two lines joined in revision 8, and they are the
# eleventh member of this plan's one recurring class. Round 7 found that a phrase
# ran straight through them into the next clause: "Enter 'abc' in the
# per-transaction limit field AFTER LOGIN" captured "per-transaction limit field
# after login", whose head is "login" -- a noun, and not a control, so the step
# with a field for its subject was read as a place and silenced. The pinned twin
# survived only because "and" happened to already be here, which is the same
# accident round 6 found in the stop-word list this set replaced.
#
# They are listed as a CATEGORY (words that open a subordinate clause), not as
# the three strings that reproduced the bug, because "fix the reported instance"
# is what has kept this plan at REVISE for seven rounds.
# THE LOCATIVE PREPOSITIONS, in one place. _LOCATIVE_PHRASE opens on them and
# _NP_BREAK closes on them, and revision 10 derives both from this tuple because
# round 9 found the FIFTEENTH member of the recurring class in the gap between
# the two hand-written lists: the opener knew "within" and "inside", _NP_BREAK
# did not, so a phrase ran straight through them and _is_a_head -- which refuses
# a break word -- had no reason to refuse either. "Enter 'abc' in the amount
# field INSIDE the dropdown" captured "amount field inside", answered on
# "inside", and went silent. Executed, not derived: the same held for "within",
# and _is_a_head accepted "outside", "near", "beyond" and "beside" too, so the
# defect was the whole category rather than the two words reported.
#
# A word here is a boundary EVERYWHERE. Only the first six also OPEN a locative
# phrase -- the one asymmetry, expressed as a slice of one tuple rather than as
# two lists that drift.
#
# That asymmetry is the SIXTEENTH member of the recurring class and a DISCLOSED
# FALSE POSITIVE: a step located by a boundary-only preposition forms no phrase
# at all, so "Below the balance card, tap Send." is flagged although it names
# its place. Widening the openers to the full tuple was proposed, on the
# prediction that the live partition would not move -- MEASURED AND REFUTED:
# the partition moves the wrong way, and TC-094 ("Enter '99999' in monthly cap
# field (above max) on debit 4521", a live clear-14-shaped field case) goes
# SILENT because "above max" becomes a location. The widening trades a
# disclosed false positive for an undisclosed false negative, and it does not
# even close its own class: "Over the account summary panel, tap Edit." stays
# flagged widened, because its head is the generic container "panel" with no
# Capitalised name. Kept as-is and pinned in BOTH directions in op 5 -- exactly
# how the quantity residual is handled. The measured partitions live in the
# plan's "Known false positives" (section 3), not here, so that a shipped
# comment cannot go stale about a suite it cannot see; re-take them in both
# directions before retrying the widening.
_LOCATIVE_PREPS = (
    "in",
    "on",
    "under",
    "within",
    "inside",
    "at",
    # ... boundary-only from here: they end a noun phrase but do not open one.
    "near",
    "outside",
    "beside",
    "along",
    "beyond",
    "above",
    "below",
    "over",
)
_LOCATIVE_OPENERS = "|".join(_LOCATIVE_PREPS[:6])

_NP_BREAK = (
    "for|and|or|but|the|a|an|with|from|by|into|onto|via|"
    "then|that|while|as|is|are|was|be|than|per|"
    "after|before|until|during|without|when|if|unless|because|since|"
    "through|across|between|about|upon|against|toward|towards|off|"
    + "|".join(_LOCATIVE_PREPS)
)

# RESIDUAL, disclosed rather than tuned: "off" joined this set in revision 9 to
# stop a phrase answering on the particle in "Toggle the ... switch OFF", and it
# also splits the two-word compounds that legitimately contain it -- "cut off
# limit field", "sign off screen", "drop off point". In _PHRASE_END the phrase
# then closes early and answers on the modifier; in _CONTROL_SUFFIX the compound
# scan stops, so "the merchant site sign off button" keeps its off-app
# exemption. Round 9 checked all three consumers and both directions resolve
# toward SILENCE, so no false positive is created -- which is why this is a note
# and not a fix. A preceding-token exception (cut|sign|drop|kick|pay) is the
# obvious tuning and is deliberately not taken: it is a word list standing in for
# a rule, which is the mistake this file has made most often.

# A following VERB also ends the phrase -- except a word this detector already
# recognises as a NOUN, because inside a noun phrase that word is the HEAD, not a
# verb. "Open the Freeze toggle" ended at "toggle" and anchored on "Freeze"; "the
# payment gateway automatic retry attempt counter" ended at "attempt" and
# anchored on "retry". Subtracting the noun sets is the rule, so adding a word to
# any of them can never reopen this by hand-edit.
_PHRASE_END_VERBS = """
    tap taps click enter select change set toggle verify check press type swipe
    scroll leave reduce increase disable enable open attempt make remove add
    apply submit save choose
""".split()
_PHRASE_END = (
    r"(?=\s*[,.;:()]|\s+(?:"
    + _NP_BREAK
    + r")\b|\s+(?:"
    + "|".join(
        sorted(set(_PHRASE_END_VERBS) - _NON_PLACE_HEADS - _GENERIC_CONTAINER_NOUNS)
    )
    + r")\b|$)"
)
# How far a noun phrase may run before _PHRASE_END has to close it. It is LAZY
# and UNBOUNDED, which is a rule; it used to be `{0,4}?`, which was a word window
# nobody had named, measured or tested -- round 7's F2, and a straight
# contradiction of the trap table below, where a word window is rejected for
# _CONTROL_SUFFIX on the grounds that "widening the guess is not the fix".
#
# The window failed in the direction the finding calls primary: a phrase longer
# than five tokens matched NOTHING, so "In the Spending Controls monthly
# transaction cap area, tap Save" -- a step that names a Capitalised container --
# was reported as unfindable. Unbounded, the phrase closes where the sentence
# closes it and the head test decides, which is the same rule the other four
# boundaries now use.
#
# Cost, for ALL THREE scanners rather than this one -- round 9's note, and the
# same omission round 8 made when this paragraph covered _PHRASE_TAIL but not
# _CONTROL_SUFFIX. The three are: the phrase patterns (this tail, run by
# finditer), _CONTROL_SUFFIX (matched per off-app hit and per screen noun), and
# _PHRASE_HERE (matched per screen noun inside an any()). All three are lazy
# repetitions over token classes disjoint from whitespace, so each end position
# has exactly one derivation -- no ambiguity, and `$` in _PHRASE_END guarantees
# termination.
#
# MEASURED, not argued: find_unanchored_first_steps over the 96-case live suite
# takes 2.3 ms end to end. Adversarial single steps -- an "of to" chain of 60, a
# 40-fold repeated screen noun, a 25-fold nominal run, and 200 words with no
# boundary at all -- each cost under 0.5 ms. There is no input in this shape that
# costs a tester anything.
#
# The tail REFUSES to cross a quantity, and _PHRASE_HERE (the screen path) is
# allowed to. That asymmetry is the most expensive line in this file: round 9
# challenged it and proposed removing it, and revision 10 tried THREE ways to
# remove it. All three are recorded, with their measurements, because the next
# reader will otherwise retry one:
#
#   (a) let _PHRASE_END CLOSE on a quantity (revision 9's attempt) -- broke 24 of
#       this file's tests: "on debit 4521" closed as "debit", so a card number
#       read as a place.
#   (b) cross a quantity everywhere, on round 9's argument that _is_a_head
#       refuses a number so one can never BE the head. MEASURED AND REFUTED:
#       trimming the number hands the head to the token before it, and "debit"
#       is in no not-a-place set. Live clear-14 fell to 7/14, partition to
#       11/79/6.
#   (c) cross a quantity only when a word follows it. Also refuted, and more
#       interestingly: TC-021 ("...on debit 4521 WHILE capturing network
#       traffic") crossed, closed at "while", trimmed back to "debit" and went
#       silent -- so (c) is (b) with extra steps. It also cost the screen path
#       four anchors ("From Card controls debit 4521, enter '1000' in monthly
#       cap") because a quantity before a COMMA is not crossable, which is
#       exactly what _PHRASE_HERE needs to cross. Partition 31/52/13, 13/14.
#
# So the asymmetry stands, and the reason is now stated rather than assumed: the
# three place patterns START AT A PREPOSITION and guess where the phrase is, so a
# number is the only signal that the guess has left the noun phrase; _PHRASE_HERE
# starts at a word already known to be a screen noun, so it can cross a number
# and still answer on a real head. The disclosed residual is in the plan's
# "Known false positives" and pinned in op 5.
#
# The PARTICIPLE stop is likewise refused only in HEAD position, by _is_a_head:
# stopping the tail at one would make "In the cards PENDING review list, tap the
# first card" match nothing, which is the same false positive as the residual
# above.
#
# The PARTICIPLE stop is still not shared with _CONTROL_SUFFIX, and that stays a
# decision with a reason: the two read in opposite directions. _CONTROL_SUFFIX
# runs LEFT to RIGHT for the noun that ENDS a compound, so stopping early keeps a
# step exempt; this bounds a phrase whose HEAD decides, so stopping early would
# match nothing at all -- and "In the cards PENDING review list, tap the first
# card" would become the same false positive as the 2024 case above. A participle
# is refused only in HEAD position, by _is_a_head, and that string is pinned.
_PHRASE_TAIL = r"(?:\s+(?![\w-]*\d)[A-Za-z'\u2019>/-]+)*?"


def _bare(word: str) -> str:
    """A phrase token stripped of quotes and lowercased, for set lookups."""
    return word.strip("'\u2019\"").lower()


# The nouns this detector knows by name. A word here is a HEAD wherever it
# stands, even when it also looks like something else -- "setting" and "settings"
# end in "ing" and are not participles, and that collision is why the participle
# test below consults this set FIRST rather than pattern-matching in isolation.
_KNOWN_NOUNS = _NON_PLACE_HEADS | _GENERIC_CONTAINER_NOUNS

# A token that cannot be the head of a noun phrase: a function word that opens a
# new phrase, the two compound-builders, or a participle. Derived from _NP_BREAK
# rather than restated -- a boundary word and a non-head are the same policy
# question asked twice, and this plan's whole history is what happens when two
# code paths answer it separately.
_NOT_A_HEAD = frozenset(_NP_BREAK.split("|")) | {"of", "to"}
_PARTICIPLE = re.compile(r"[\w-]*ing", re.IGNORECASE)


def _is_a_head(word: str) -> bool:
    """True when `word` can be the HEAD NOUN of a phrase.

    _head_is_a_place used to ask only "is this word in a not-a-place set", which
    silently assumed the last word was a noun at all. Round 7's F1: "Enter 'abc'
    in the amount field TO save it" captured "amount field to" and answered on
    "to", and "in the monthly cap field EXCEEDING the limit" answered on a
    participle. Neither is a noun, so neither can carry the phrase.
    """
    if not word:
        return False
    if word in _KNOWN_NOUNS:
        return True
    if word in _NOT_A_HEAD:
        return False
    if any(ch.isdigit() for ch in word):
        # A QUANTITY is not a head noun -- "on debit 4521" is about the card, not
        # about 4521. This lived in _head_is_a_place as a `head[0].isdigit()`
        # special case that only rejected the phrase instead of trimming to the
        # real head, which is why "Enter 5000 in the toolbar 2 field" could not be
        # judged on "field" at all (round 8's fourteenth-member fix, revision 9).
        return False
    if word in ("http", "https") or word.startswith(("http://", "https://")):
        # A URL has exactly ONE judge -- _url_names_a_destination -- because
        # deciding it needs to know what the step DOES with it, which a head
        # noun cannot express. Revision 8's own audit caught the nav path
        # answering that question a second time: _NAV_TO_NAMED's word capture
        # stops at the ":" and offers "https" as a head, so "Open https://evil...
        # in the redirect link field" was DATA to the URL rule and a PLACE to the
        # nav rule. Same class as F6, found within the hour of fixing F6 -- which
        # is why it is excluded HERE, in the shared test, and not at that one
        # call site.
        return False
    return not _PARTICIPLE.fullmatch(word)


def _head_is_a_place(phrase: str) -> bool:
    """The ONE head-noun test. Every place predicate goes through it.

    A noun phrase names a place unless its HEAD -- the last word -- is a control
    ("the account number FIELD"), a device ("on IPHONE"), a duration ("within 60
    SECONDS"), a bare number ("on debit 4521") or an UNNAMED generic container
    ("in the monthly cap SECTION").

    It is one function on purpose. Revision 5 applied these exclusions inside the
    locative path only, and the other two predicates -- the nav verb and
    "from <place>" -- silently disagreed with it: "Open the 'Per-transaction
    limit' field" anchored on the quoted field name, and "Change the limit from
    the iPhone" anchored on a handset. That is the same defect round 5 found
    between _SCREEN_NOUN and the locative path, one layer over. A shared test is
    the only shape in which those paths cannot drift apart again.
    """
    words = phrase.split()
    # Trim back to the real head: a trailing function word or participle is not
    # a noun, so it cannot be what the phrase is ABOUT. "amount field to" is a
    # field; "monthly cap field exceeding" is a field. Revision 8, round 7's F1.
    while words and not _is_a_head(_bare(words[-1])):
        words = words[:-1]
    if not words:
        return False
    head = _bare(words[-1])
    if not head:
        return False
    if head in _GENERIC_CONTAINER_NOUNS:
        # The QUALIFIER has to carry the name, never the generic head itself:
        # "In the Section, tap Freeze" is capitalisation, not a name. And a
        # generic container cannot be named by ANOTHER generic container, which
        # is what let "In the Settings panel" anchor while "Navigate to Settings"
        # stayed weak -- one word, two verdicts, round 6's F4.
        qualifier = " ".join(
            w for w in words[:-1] if _bare(w) not in _GENERIC_CONTAINER_NOUNS
        )
        return bool(_NAMED_QUALIFIER.search(qualifier))
    return head not in _NON_PLACE_HEADS


# The opener carries the same quantity guard as the tail and as _FROM_PLACE,
# which is the THIRTEENTH member of the recurring class and was found by asking
# the enumeration question of revision 8's own fix: _FROM_PLACE opened with
# [A-Za-z] and these two opened with [A-Za-z0-9], so one predicate refused a
# phrase beginning in a quantity and two accepted it. Live TC-032 ("At 00:05
# Asia/Riyadh ON 1ST OF MONTH query backend monthly spend API...") anchored on a
# DATE for exactly that reason -- silent either way, since the case is off-app,
# but silent for a reason that would not survive being read aloud.
_PHRASE_START = r"(?![\w-]*\d)[A-Za-z'\u2019>/-]+"
_LOCATIVE_PHRASE = re.compile(
    r"\b(?:" + _LOCATIVE_OPENERS + r")\s+(?:the\s+|an?\s+|your\s+|its\s+)?"
    r"(" + _PHRASE_START + _PHRASE_TAIL + r")" + _PHRASE_END,
    re.IGNORECASE,
)

# A navigation verb anchors only when its destination is a PLACE. "Open the
# per-transaction limit field" is a field, and treating any "Open ..." as
# sufficient is the mistake the prompt itself calls out ("open any upload
# section").
#
# The capture runs to the end of the noun phrase rather than to the first token,
# because the head is what decides and it is often preceded by the name: in
# "Open the 'Per-transaction limit' field" the quoted part is the field's NAME
# and "field" is the head. Stopping early anchored every quoted control in the
# suite -- round 6's largest leak.
#
# It used to require a quoted or Capitalised destination, and this pattern was
# the one case-SENSITIVE regex in the detector because of it. That requirement
# was a PROXY for "is this a place or a control", written before a direct test
# existed; _head_is_a_place is that direct test, so the proxy is gone. Measured:
# dropping it changes nothing on the live suite (23/58/15, 14/14) and nothing on
# any weak fixture, and it closes the case split round 6 reported -- "in the
# cards list" was silent while "navigate to cards" was weak, purely because the
# locative patterns were IGNORECASE and this one was not.
_NAV_VERBS = r"navigate to|go to|open|launch|return to|back to"
_NAV_TO_NAMED = re.compile(
    r"\b(?:" + _NAV_VERBS + r")\s+(?:the\s+)?"
    r"((?:'[^']+'|\"[^\"]+\"|"
    + _PHRASE_START
    + r")"
    + _PHRASE_TAIL
    + r")"
    + _PHRASE_END,
    re.IGNORECASE,
)

# On a web suite the URL IS the location -- but only when the tester GOES there.
# A URL the tester TYPES is data: "Enter https://... in the URL field and tap Go"
# opens inside a field like any other clear-14 case, and "Type https://evil... in
# the redirect link field" is a security case whose whole point is the field.
#
# Round 7 found this the same way round 6 found _names_a_screen: by enumerating
# every truthy source in _names_a_place and asking which ones skip the shared
# tests. A bare https?:// scan was the last one -- the FIFTH place path, and the
# fourth appearance of "a keyword short-circuits ahead of the head test".
#
# So the URL must be taken by a NAVIGATION verb, or stand at the head of the step
# as its starting point. _NAV_VERBS is shared with _NAV_TO_NAMED above rather
# than restated, because a nav-verb list in two places is this plan's oldest
# recurring bug in miniature.
#
# Revision 8 also consumes the URL TOKEN itself, so the caller can read what the
# step does with it. Round 7's F6: requiring a nav verb left the verdict resting
# on the verb ALONE -- "Type https://evil... in the redirect link field" was weak
# and "Open https://evil... in the redirect link field" anchored, though the URL
# plays the same role in both. A destination the step immediately puts INTO a
# control is data, whatever verb introduced it.
#
# A LOCATIVE preposition opens one too ("In https://app.example.com/cards, tap
# Freeze"), which round 8 found reported as unfindable: it matched no nav verb,
# did not open the step, and no head test could rescue it because _is_a_head
# refuses a URL by design. That was a false POSITIVE with no disclosure, which is
# the one direction this advisory check must not fail in silently.
_URL_DESTINATION = re.compile(
    r"(?:\b(?:" + _NAV_VERBS + r")\s+(?:the\s+)?"
    r"|\b(?:" + _LOCATIVE_OPENERS + r"|from)\s+"
    r"|^\s*)https?://[^\s,;]*",
    re.IGNORECASE,
)

# "From <place>" anchors -- but "from SAR 3,000 to SAR 5,000" introduces a
# VALUE, not a location. Measured: without this guard it falsely anchored two of
# the clear weak cases. Currency-agnostic: an allow-list of SAR/USD/EUR let
# "from GBP 3,000 to GBP 5,000" through as a place.
_FROM_PLACE = re.compile(
    r"\bfrom\s+(?![A-Za-z]{3}\s*[\d.,])(?!\d)(?:the\s+)?"
    r"(" + _PHRASE_START + _PHRASE_TAIL + r")" + _PHRASE_END,
    re.IGNORECASE,
)


def _any_phrase_is_a_place(pattern, text: str) -> bool:
    """True when ANY phrase this pattern captures has a place for a head."""
    return any(
        _head_is_a_place((m.group(1) or "").strip()) for m in pattern.finditer(text)
    )


def _url_names_a_destination(text: str) -> bool:
    """True when a URL in `text` is somewhere the tester GOES, not data.

    A nav verb is necessary and was, until revision 8, sufficient -- which left
    the verb deciding a question about the URL's ROLE (round 7's F6). A URL the
    step then puts into a control is data: "Open https://evil... IN THE REDIRECT
    LINK FIELD" is the security case whose whole point is the field, and it must
    read the same as the "Type ..." twin already pinned beside it.

    The test is _head_is_a_place on what follows, so the URL path finally reaches
    the same shared head rule as every other place predicate -- it was the last
    one that did not.
    """
    for match in _URL_DESTINATION.finditer(text):
        rest = text[match.end() :]
        # ONE scan of the remainder, not two. Revision 8 called .search() and
        # then _any_phrase_is_a_place(), which ran finditer over the same text a
        # second time -- round 8's note, and the only place the nested-scan cost
        # was real.
        phrases = [(m.group(1) or "").strip() for m in _LOCATIVE_PHRASE.finditer(rest)]
        if phrases and not any(_head_is_a_place(p) for p in phrases):
            continue
        return True
    return False


# The tester-facing report is a COUNT ONLY, and that is a budget decision, not
# a style one.
#
# WHAT BOUNDS THIS LINE. The reply is not bounded by one fixed number.
# tools/mcp_handlers caps the WHOLE submit reply at _REPLY_CAP and gives
# `summary` -- the block this line lives in -- a COMPUTED allowance:
#
#   summary_budget(sections, header_len)
#       = max(_SUMMARY_FLOOR, min(_SUMMARY_CAP, _REPLY_CAP - overhead))
#       overhead = every other section + the header + _TRUNC_RESERVE
#
# so the allowance is clamped into [_SUMMARY_FLOOR, _SUMMARY_CAP] and is
# STRICTER than the old fixed ceiling whenever the reply carries notes -- never
# looser. tests/test_finalize_reply_cap.py still measures the summary against
# that fixed ceiling, which is the WORST case for this line, so its two
# fixtures remain the right yardstick.
#
# THE COUNT FITS AND THE IDS DO NOT. The id form costs 344 chars where this
# line costs 64, against a measured headroom of 325 (all-plans fixture) and 296
# (mixed). Each cost is the line PLUS the one newline the surrounding join also
# adds, which is why a 63-char line moves the total by 64. The line is 64 chars
# on the live run ("23 of 96") and 63 in the cap fixture ("1 of 96") -- its
# length tracks the DIGITS of the two numbers, 62 for "7 of 8" and 68 for
# "1234 of 5678". Nothing pins an exact length: the test bounds it at <= 80.
#
# NO EXIT CONDITION -- and that is a MEASUREMENT, not an omission. Earlier
# revisions carried one: "F03 is about that cap; when it lands, swap the count
# for the id form." F03 HAS landed, and it settles the question the other way
# -- it made the allowance computed and STRICTER, so the swap over-runs both
# fixtures rather than fitting. It becomes affordable only if the BASELINE
# drops by roughly 300 chars, not if the cap moves.
#
# THIS LINE SURVIVES TRUNCATION. `summary` is the block F03 makes yield, so
# "does a tight budget eat this line?" is a real question, and it is answered:
# the Data Quality Notes block is the FIRST section of the summary and this
# line sits about 13% into it. Truncation is a head slice, so the line is
# inside every allowance down to _SUMMARY_FLOOR -- it cannot be the thing that
# is lost. That cross-module claim is PINNED by
# test_the_budget_this_line_was_sized_against_still_works_that_way, because a
# comment cannot fail and this one already went stale once.
#
# The four measured reply forms, the exact byte offsets and the commit each
# figure was taken at are in the plan's section 2b, deliberately not here: a
# figure about another module's tree is the one thing a comment beside this
# constant cannot keep true.
FINDABILITY_MAX_LOGGED = 40

# A word is the MODIFIER of an in-app control when the compound noun it starts
# ends in a control noun: "the ATM withdrawal LIMIT", "the merchant site FIELD",
# "the ATM locator ICON", "the menu ICON", "the toolbar FIELD". Both the off-app
# keywords and the screen nouns need exactly this test, so there is exactly one
# of it -- see the caller list in its docstring.
#
# WHERE THE COMPOUND ENDS is the whole difficulty, and rounds 5 and 6 each broke
# a different answer to it:
#
#   {0,2} words (rev 5)  missed "payment gateway automatic retry attempt COUNTER"
#   {0,5} words          cancelled the genuine exemption in "ATM withdrawal SAR
#                        500 exceeding the daily limit" -- a wide window reaches
#                        into the next clause
#   a stop-word list     (rev 6) was wrong in BOTH directions: it ended the
#                        compound at "of"/"to", so "merchant site terms OF
#                        service checkbox" stayed exempt, and it did NOT end at
#                        "exceeding", so "ATM withdrawal SAR 500 exceeding daily
#                        limit" lost its exemption as soon as the article was
#                        dropped. Every fixture that scored 5/5 for it happened
#                        to contain an article -- an artifact, not a measurement.
#
# So the rule is inverted, per round 6's suggestion: a compound CONTINUES across
# nominal material and stops at the three things that end a noun phrase --
# punctuation, a QUANTITY (any token carrying a digit), and a new clause opened
# by a function word or a PARTICIPLE. "of" and "to" are the two function words
# that build genuine compounds ("terms of service", "time to live", "point of
# sale"), so they are the two allowed through.
#
# RESIDUAL, stated rather than tuned away: a participle can also be a legitimate
# noun modifier ("the merchant site SPENDING limit field"), so that compound
# stops early and the step stays exempt. Both remaining inexactnesses in this
# boundary resolve toward SILENCE, which is the direction this advisory check
# chooses everywhere else -- a false positive spends a tester's attention, a
# false negative costs them nothing they had before.
#
# _NP_BREAK is shared with _PHRASE_END on purpose; see its comment.
#
# The compound ends at any noun this detector does not treat as a place -- the
# same _KNOWN_NOUNS _head_is_a_place tests, not the _CONTROL_NOUNS subset it used
# to name. That subset was round 7's F3, the TWELFTH member of the recurring
# class: "in the monthly cap section" was weak (the locative path applied the
# generic-container rule) while "in the menu section" anchored, because "section"
# was not a control noun, so _is_modifier_of_a_control said no and _names_a_screen
# short-circuited on "menu" before the head test could ever run. One policy --
# "which nouns are not places" -- was answered by two sets, which is the only bug
# this plan has ever had.
# The repetition below used to carry a second alternative, `|(?:of|to)\s+`, to
# let a compound cross the two words that build one. It was REDUNDANT and
# DANGEROUS, and revision 9 deleted it. Redundant because "of" and "to" are
# deliberately absent from _NP_BREAK, so the first branch already accepts them --
# they are not break words, carry no digit and do not end in "ing". Dangerous
# because two branches that match the same input make the group the classic
# ambiguous `(a|a)*?`: a step carrying a long chain of "of"/"to" with no
# terminating noun makes the FAILURE path explore 2**k derivations. This function
# runs on every finalize over model-produced text, and the try/except in
# find_unanchored_first_steps catches exceptions -- it cannot catch a hang.
# Round 8 found it; the fix is a deletion, and the behaviour is identical.
_CONTROL_SUFFIX = re.compile(
    r"^[\s'\u2019\"-]*"
    # ... across nominal words: not a break word, not a participle, no digit
    r"(?:(?!(?:" + _NP_BREAK + r")\b)(?![\w-]*\d)(?![\w-]*ing\b)"
    r"[\w\u2019'-]+\s+)*?"
    r"(?:" + "|".join(sorted(_KNOWN_NOUNS)) + r")\b",
    re.IGNORECASE,
)

# The phrase that STARTS at a given offset, using the same opener, tail and
# boundary as the three place patterns. _names_a_screen needs it: a screen noun
# is only a screen when the phrase it heads is a place, and until revision 9 that
# path answered without ever consulting _head_is_a_place.
#
# Its tail is the ONE that may cross a quantity -- see the three refuted attempts
# in _PHRASE_TAIL's comment above for why that is a measurement rather than a
# preference. "Enter 5000 in the toolbar 2 FIELD" has to reach "field" to be
# judged, and "From Card controls debit 4521, ..." has to reach past a card
# number to keep its anchor.
_PHRASE_HERE = re.compile(
    r"(" + _PHRASE_START + r"(?:\s+[A-Za-z0-9'\u2019>/-]+)*?)" + _PHRASE_END,
    re.IGNORECASE,
)


def _is_modifier_of_a_control(tail: str) -> bool:
    """True when `tail` continues a compound noun that ends in a control noun.

    Callers -- and this list is the claim, so it is enumerated rather than
    counted from memory:
      1. _off_app_match, so "the merchant site FIELD" is not a merchant visit
      2. _names_a_screen, so "the menu ICON" is not the menu

    Round 6 found (2) missing: _names_a_screen was a bare _SCREEN_NOUN substring
    scan sitting AHEAD of every other predicate, so "Tap the menu icon", "Enter
    5000 in the toolbar field" and "Toggle the footer switch OFF" all anchored on
    a word that was modifying a control -- structurally the same bare-keyword
    class this detector eradicated for "terminal" in round 3.
    """
    return bool(_CONTROL_SUFFIX.match(tail))


def _screen_noun_heads_a_place(probe: str, start: int) -> bool:
    """True when the phrase beginning at `start` has a place for its head.

    This is the FOURTEENTH member of this plan's recurring class and the one it
    kept coming back to: _names_a_screen was the last truthy source of "place"
    that never reached _head_is_a_place. It was guarded only by
    _is_modifier_of_a_control, and round 8 showed that guard failing OPEN in
    exactly the cases revision 8 had just taught it to refuse -- _CONTROL_SUFFIX
    stops at a participle and at a quantity, so "Enter 2500 in the menu SPENDING
    limit field" and "Enter 5000 in the toolbar 2 field" cleared the modifier
    test with zero iterations and the screen noun was promoted to a screen. The
    step is a FIELD; its pinned twin "Toggle the parental controls switch OFF"
    was flagged. One rule answering in two directions, again.

    Both guards are kept rather than one replacing the other, because they ask
    different questions: the modifier test reads FORWARD for the noun that ends a
    compound, this reads the phrase the screen noun HEADS. A screen noun counts
    only if it survives both.
    """
    match = _PHRASE_HERE.match(probe, start)
    return bool(match) and _head_is_a_place(match.group(1).strip())


def _names_a_screen(text: str) -> bool:
    """True when the text names a screen outright (a screen noun, or a URL).

    A screen noun counts only where it is not MODIFYING a control: "the Cards
    tab" is a place, "the menu icon" is a button. Every occurrence is tried,
    because one qualified mention must not hide an unqualified one.

    A URL counts only where a navigation verb takes it, or where it opens the
    step, AND the step does not then put it into a control -- see
    _url_names_a_destination. All of those are the same rule: a keyword is not a
    location just because it appears.
    """
    if not text:
        return False
    if _url_names_a_destination(text):
        return True
    probe = _SCREEN_READER.sub(" ", text)
    return any(
        not _is_modifier_of_a_control(probe[m.end() :])
        and _screen_noun_heads_a_place(probe, m.start())
        for m in _SCREEN_NOUN.finditer(probe)
    )


def _names_a_place(text: str) -> bool:
    """True when the text names a place the tester can physically be.

    For a STEP only. Preconditions get _names_a_screen instead: they are free
    prose dense with prepositions, and the locative rule reads "logged in with
    push enabled" or "test at month start Asia/Riyadh" as locations. Measured on
    the live run, the permissive form anchored 4 cases on preconditions and not
    one of them named a screen.
    """
    if not text:
        return False
    if _names_a_screen(text):
        return True
    return (
        _any_phrase_is_a_place(_NAV_TO_NAMED, text)
        or _any_phrase_is_a_place(_LOCATIVE_PHRASE, text)
        or _any_phrase_is_a_place(_FROM_PLACE, text)
    )


def _off_app_match(action: str):
    """The first off-app match that is not the MODIFIER of an in-app control."""
    for pattern in (
        _OFF_APP_FIRST_STEP,
        _OFF_APP_TRANSACTION,
        _OFF_APP_MESSAGE,
        _AUTH_BOUNDARY,
    ):
        for match in pattern.finditer(action):
            if _is_modifier_of_a_control(action[match.end() :]):
                continue
            return match
    return None


def _first_step_is_off_app(action: str) -> bool:
    """True when the step is not app navigation and so has no screen to name."""
    return _off_app_match(action) is not None


def find_unanchored_first_steps(cases: list[TestCase]) -> list[tuple[str, str]]:
    """Return (tc_id, action) for every case whose FIRST step names no place.

    The case's PRECONDITIONS count as an anchor too -- a tester reads them
    before step 1, so "User is on the Card controls screen" makes the case
    findable no matter how the step is phrased. They are held to the STRICTER
    test (_names_a_screen): preconditions are prose, and the step-level locative
    rule reads "logged in with push enabled" as a location.

    Anchored is checked BEFORE off-app on purpose: a step that names its screen
    is fine even when it also mentions a terminal ("Disable ATM withdrawals on
    debit 4521 Card controls"). The other order exempts that case for the wrong
    reason and makes the exemption list load-bearing where it should not be.

    Never raises.
    """
    try:
        out: list[tuple[str, str]] = []
        for tc in cases:
            steps = getattr(tc, "steps", None) or []
            if not steps:
                continue
            action = str(getattr(steps[0], "action", "") or "")
            if not action.strip():
                continue
            preconditions = str(getattr(tc, "preconditions", "") or "")
            if _names_a_place(action) or _names_a_screen(preconditions):
                continue
            if _first_step_is_off_app(action):
                continue
            out.append((str(getattr(tc, "tc_id", "") or ""), action))
        return out
    except Exception:
        logger.exception("find_unanchored_first_steps failed — returning empty list")
        return []


def quality_ratio(cases: list[TestCase]) -> float:
    """Fraction of steps that are vague and/or have placeholder test data. Never raises."""
    try:
        total_steps = sum(len(tc.steps) for tc in cases)
        if total_steps == 0:
            return 0.0
        flagged_steps: set[tuple[str, int]] = set()
        for tc_id, step_number, _ in find_vague_steps(cases):
            flagged_steps.add((tc_id, step_number))
        for tc_id, step_number, _ in find_placeholder_data(cases):
            flagged_steps.add((tc_id, step_number))
        for tc_id, step_number, _ in find_vague_expected(cases):
            flagged_steps.add((tc_id, step_number))
        return len(flagged_steps) / total_steps
    except Exception:
        logger.exception("quality_ratio failed — returning 0.0")
        return 0.0


def quality_warning_section(cases: list[TestCase], max_examples: int = 10) -> str:
    """Build a '## Data Quality Notes' markdown block flagging vague/placeholder
    content that survived generation. Returns '' when nothing is flagged or on
    any internal error. Never raises."""
    try:
        vague = find_vague_steps(cases)
        placeholder = find_placeholder_data(cases)
        vague_expected = find_vague_expected(cases)
        # Suite-level: flagged only on a suite large enough for the pattern to
        # mean something, so a one-case fixture never trips it.
        total_cases = len(list(cases))
        empty_data = find_empty_data_cases(cases)
        empty_test_data = find_empty_test_data_cases(cases)
        unanchored = find_unanchored_first_steps(cases)
        # 2026-08-21: the RATIO is taken over the empty-test_data measure, which
        # does not also require empty preconditions. The old conjunction meant
        # this advisory could only fire on a suite that was ALSO missing its
        # preconditions, so the live 90.6%-empty run was invisible to it.
        # BOTH-empty is a subset of test_data-empty, so this is a strict
        # superset of the old gate; empty_data survives as the detail count.
        empty_data_flagged = (
            total_cases >= EMPTY_DATA_MIN_CASES
            and len(empty_test_data) / total_cases >= EMPTY_DATA_WARN_RATIO
        )
        if (
            not vague
            and not placeholder
            and not vague_expected
            and not empty_data_flagged
            and not unanchored
        ):
            return ""

        lines = ["\n\n## Data Quality Notes\n"]
        if vague:
            lines.append(
                f"- {len(vague)} step(s) may use vague phrasing instead of a literal "
                "value — please review before executing:"
            )
            for tc_id, step_number, action in vague[:max_examples]:
                lines.append(f"  - {tc_id} step {step_number}: {action[:120]}")
        if placeholder:
            lines.append(
                f"- {len(placeholder)} step(s) have placeholder test data instead of "
                "a concrete example value:"
            )
            for tc_id, step_number, test_data in placeholder[:max_examples]:
                lines.append(f"  - {tc_id} step {step_number}: {test_data}")
        if vague_expected:
            lines.append(
                f"- {len(vague_expected)} step(s) have a vague expected result "
                '(e.g. "appropriate error message") instead of the concrete outcome '
                "to verify:"
            )
            for tc_id, step_number, expected in vague_expected[:max_examples]:
                lines.append(f"  - {tc_id} step {step_number}: {expected[:120]}")
        if empty_data_flagged:
            pct = round(100 * len(empty_test_data) / total_cases)
            threshold_pct = round(100 * EMPTY_DATA_WARN_RATIO)
            # COUNTS ONLY, never an id list: this section is where the finalize
            # reply's _SUMMARY_CAP truncation lands (live repro 2026-08-15).
            # It leads with the RATIO judgement rather than the raw missing
            # count so that it does not restate data_notes_section's "N of M
            # case(s) declare a data plan; K declare none" line -- that is a
            # DISCLOSURE of a contractually legal shape, this is the advisory
            # that the shape has taken over the suite. Additive, not a second
            # rendering of the same number.
            #
            # AND IT IS BUDGETED (reviewer F1, 2026-08-21, MEASURED not
            # estimated). The suite shape that TRIPS this advisory is also the
            # shape whose Test Data enumeration has almost nothing left to
            # enumerate, so the section does not shrink enough to pay for a
            # long bullet: a first draft ran 380 chars and pushed the finalize
            # reply to 4229 against mcp_handlers._SUMMARY_CAP = 4000,
            # truncating the grounding/scope advisories and the
            # REVIEW_REQUIRED tail that print after it. One line, ~134 chars at
            # a 96-case suite. If it must grow, shorten something else here --
            # do not raise the cap.
            # tests/test_finalize_reply_cap.py measures the tripped shape.
            lines.append(
                # F13 (2026-08-30): "no test data plan" named no field, and the
                # export's Test Data column -- which renders STEP-level
                # test_data and is routinely FULL on a suite that trips this --
                # made the same tester read a 100%-missing warning beside a
                # populated column. Both are true; they measure different
                # fields. "case-level" is six characters and says which. The
                # remediation is unchanged, and so is the budget this bullet is
                # measured against: a longer explanation was written, measured
                # at +56 chars, and pushed the finalize reply past
                # _SUMMARY_CAP -- cutting this advisory AND the duplicate
                # prescreen's CONTRADICTED headline. Do not re-add it here; the
                # place for the long version is the workbook.
                f"- {pct}% of {total_cases} case(s) have no case-level "
                f"test_data (threshold {threshold_pct}%; {len(empty_data)} "
                "also lack preconditions) — add a test_data entry per field "
                "the case uses."
            )
        if unanchored:
            # The ids go to the log, not the reply: see FINDABILITY_MAX_LOGGED.
            logger.info(
                "findability: %d of %d case(s) open mid-screen (first step names no screen): %s",
                len(unanchored),
                total_cases,
                ", ".join(tc_id for tc_id, _ in unanchored[:FINDABILITY_MAX_LOGGED]),
            )
            lines.append(
                f"- {len(unanchored)} of {total_cases} case(s) open mid-screen "
                "(first step names no screen)."
            )
        return "\n".join(lines)
    except Exception:
        logger.exception("quality_warning_section failed — returning empty string")
        return ""


def resolve_chained_refs_to_stable(cases: list[TestCase]) -> list[TestCase]:
    """Rewrite category-local ``chained_from`` tc_ids to the target case's stable_id.

    Called per CategoryResult BEFORE cross-category flatten, while a tc_id still
    uniquely identifies a case WITHIN its own category (every category numbers from
    TC-001, so a raw tc_id becomes ambiguous once categories are merged). The LLM
    only ever sees its own category's ids, so a chained ref can only mean a case in
    the same batch. We resolve it to that case's content stable_id — which survives
    the flatten, dedup and the final renumber — and clear any ref that names no case
    in this category. Returns a new list; never mutates; never raises.
    """
    try:
        local = {tc.tc_id: tc.stable_id for tc in cases}
        out: list[TestCase] = []
        for tc in cases:
            if not tc.test_data:
                out.append(tc)
                continue
            new_items = []
            changed = False
            for it in tc.test_data:
                if it.strategy == "chained" and it.chained_from:
                    target = local.get(it.chained_from)
                    if target is None:
                        logger.info(
                            "resolve_chained_refs_to_stable: clearing chained_from "
                            "%r on %s field %r — no such case in category",
                            it.chained_from,
                            tc.tc_id,
                            it.field,
                        )
                        # genpipe L3: a dangling chained ref is no longer a chain —
                        # downgrade the strategy so no consumer treats it as one.
                        it = it.model_copy(
                            update={"chained_from": None, "strategy": "static"}
                        )
                        changed = True
                    elif target != it.chained_from:
                        it = it.model_copy(update={"chained_from": target})
                        changed = True
                new_items.append(it)
            out.append(
                tc.model_copy(update={"test_data": new_items}) if changed else tc
            )
        return out
    except Exception:
        logger.exception(
            "resolve_chained_refs_to_stable failed — returning cases unchanged"
        )
        return cases


def restore_chained_refs_from_stable(cases: list[TestCase]) -> list[TestCase]:
    """Rewrite stable_id ``chained_from`` values back to the FINAL renumbered tc_id.

    Counterpart to resolve_chained_refs_to_stable, run AFTER the final TC-001..N
    renumber. Each chained_from now holds the target case's stable_id (set at the
    per-category boundary); map it to that case's final tc_id. A stable_id no longer
    present (target deduped/dropped) is a dangling ref — cleared to None and logged
    so testers never see a wrong prerequisite pointer. New list; never raises.
    """
    try:
        by_stable = {tc.stable_id: tc.tc_id for tc in cases}
        out: list[TestCase] = []
        for tc in cases:
            if not tc.test_data:
                out.append(tc)
                continue
            new_items = []
            changed = False
            for it in tc.test_data:
                if it.strategy == "chained" and it.chained_from:
                    final_id = by_stable.get(it.chained_from)
                    if final_id is None:
                        logger.info(
                            "restore_chained_refs_from_stable: clearing dangling "
                            "chained_from on %s field %r (target case dropped)",
                            tc.tc_id,
                            it.field,
                        )
                        # genpipe L3: downgrade a dangling chain to a plain static
                        # value so no exporter renders a broken prerequisite.
                        it = it.model_copy(
                            update={"chained_from": None, "strategy": "static"}
                        )
                        changed = True
                    elif final_id != it.chained_from:
                        it = it.model_copy(update={"chained_from": final_id})
                        changed = True
                new_items.append(it)
            out.append(
                tc.model_copy(update={"test_data": new_items}) if changed else tc
            )
        return out
    except Exception:
        logger.exception(
            "restore_chained_refs_from_stable failed — returning cases unchanged"
        )
        return cases


_MODULE_SEPARATORS = ("-", "\u2013", "\u2014", ":", ">", "/", "|")


def _qualifier_prefix_merges(counts: dict[str, int]) -> dict[str, str]:
    """Map a QUALIFIER-PREFIXED module label onto the bare label it qualifies.

    ``counts`` is {exact module label: number of cases}. Returns
    {qualified_label: bare_label} for the safe subset only; every other pair is
    left alone.

    2026-08-03. normalize_module_names' first pass merges only CASING/whitespace
    variants, so a real suite shipped one feature under two labels:
    "Cancel Order" (86 cases) and "Sehhaty Store - Cancel Order" (12) --
    different bucket keys, never merged, and every "group by module" view (Jira,
    TestRail, the XLSX pivot) fragmented for what is one feature.

    Merges ONLY on TAIL containment -- the bare label must be the TRAILING segment
    of the qualified one, after a separator:

        "Cancel Order"  <- "Sehhaty Store - Cancel Order"   MERGE

    and NEVER on HEAD containment, which is how a genuine SUB-module is named:

        "Store Wallet"  <- "Store Wallet - Top Up"          REFUSE
        "Checkout"      <- "Checkout - Guest"               REFUSE
        "Profile"       <- "Profile: Addresses"             REFUSE

    That asymmetry IS the rule, and it is why plain containment (or a token-count
    threshold) cannot work: in "<qualifier> - <thing>" naming the trailing segment
    is the thing and the leading segment is its scope (product, app, area). Two
    labels sharing a TAIL are one thing at two qualification depths; two labels
    sharing a HEAD are different things inside one area.

    Second guard -- refuse when TWO OR MORE distinct qualified labels share one
    tail. "Admin - Login" and "User - Login" both tail-match "Login", and
    collapsing them would destroy precisely the distinction those labels encode.
    Only a 1:1 qualified->bare mapping merges.

    Script-agnostic on purpose: matching is on separator-delimited segments, never
    on casing, so it behaves identically for the Arabic labels that make up a
    large share of real suites (``str.casefold()`` is a no-op for Arabic, which
    the first pass silently depends on).
    """

    def _norm(label: object) -> str:
        return " ".join(str(label or "").split())

    # Keyed CASEFOLDED, and the winner within a key is the spelling used by the
    # MOST cases. 2026-08-03: this lookup used to be exact-text, which silently
    # defeated the whole rule the first time a real suite disagreed on case. The
    # observed split was "Cancel Order" (49) + "Sehhaty Cancel Order" (10) +
    # "Sehhaty Store - Cancel order" (20): the qualified label's tail is
    # "Cancel order" but the bare label present is "Cancel Order", so the exact
    # match failed and _qualifier_prefix_merges returned {} for exactly the
    # three-way split it exists to close. Majority spelling matches what the
    # casing pass already does, so the two passes cannot disagree on the winner.
    bare_by_key: dict[str, str] = {}
    for label in counts:
        key = _norm(label).casefold()
        cur = bare_by_key.get(key)
        if cur is None or counts.get(label, 0) > counts.get(cur, 0):
            bare_by_key[key] = label

    # Qualifier tokens seen in SEPARATOR-qualified labels, e.g. "Sehhaty Store - X"
    # contributes {sehhaty, store}. Used only to decide whether a SEPARATOR-LESS
    # label is a product-qualified variant; see the guard below.
    known_qualifiers: set = set()
    # tail_key -> {qualified label: the token set that was REMOVED to reach the tail}.
    # The removed tokens are what tell one product family from rival qualifiers.
    tails: dict[str, dict[str, frozenset]] = {}
    for label in counts:
        norm = _norm(label)
        for sep in _MODULE_SEPARATORS:
            token = f" {sep} "
            idx = norm.find(token)
            while idx != -1:
                head = norm[:idx].strip()
                tail = norm[idx + len(token) :].strip()
                if tail and tail != norm:
                    toks = frozenset(t.casefold() for t in head.split() if t)
                    tails.setdefault(tail.casefold(), {})[label] = toks
                    known_qualifiers.update(toks)
                idx = norm.find(token, idx + 1)

    # A SEPARATOR-LESS label can still be a product-qualified variant:
    # "Sehhaty Cancel Order" is "Cancel Order" with a product name glued on. But
    # plain suffix containment is exactly the dangerous rule -- "Order" is a suffix
    # of "Cancel Order" and merging those would be wrong. The discriminator is
    # WHAT was removed: allow it only when every removed prefix token is already a
    # known qualifier token from a separator-qualified label in this same suite.
    # "Sehhaty" qualifies via "Sehhaty Store - ...", so it is allowed; "Cancel"
    # never appears as a qualifier, so "Order" <- "Cancel Order" stays refused.
    for label in counts:
        norm = _norm(label)
        toks = norm.split()
        for cut in range(1, len(toks)):
            prefix = toks[:cut]
            tail = " ".join(toks[cut:])
            if not tail:
                continue
            if not all(t.casefold() in known_qualifiers for t in prefix):
                continue
            tails.setdefault(tail.casefold(), {})[label] = frozenset(
                t.casefold() for t in prefix
            )

    merges: dict[str, str] = {}
    for tail_key, qualified in tails.items():
        target = bare_by_key.get(tail_key)
        if target is None:
            continue
        others = {q: toks for q, toks in qualified.items() if q != target}
        if not others:
            continue
        # ONE qualifier family, or rivals? 2026-08-03: the previous rule refused any
        # tail claimed by more than one qualified label, which correctly rejects
        # "Admin - Login" + "User - Login" but ALSO rejected the real observed
        # split -- "Sehhaty Cancel Order" + "Sehhaty Store - Cancel order" both
        # point at "Cancel Order", and those are three spellings of ONE module, not
        # two sub-modules. The discriminator is the REMOVED tokens: a shared token
        # across every claimant means one product prefix spelled at different
        # depths (sehhaty / sehhaty+store), while disjoint tokens (admin vs user)
        # encode a distinction that merging would destroy.
        if len(others) > 1:
            common = frozenset.intersection(*others.values()) if others else frozenset()
            if not common:
                continue
        for q in others:
            merges[q] = target
    return merges


def normalize_module_names(
    cases: list[TestCase], merge_qualifier_prefixes: bool = False
) -> list[TestCase]:
    """Canonicalize `module` casing/whitespace across a merged suite.

    2026-08-01: a real host-mode run (8 parallel category workers, each blind
    to the others' output) produced ONE feature split across two module
    labels -- "Cancel order" (60 cases) and "Cancel Order" (36 cases) -- since
    `TestCase.module` is unconstrained free text (tools/models.py) and nothing
    merges the workers' independent choices. That fragments every "group by
    module" view (Jira, TestRail, the XLSX pivot) for a suite that is really
    one feature.

    Groups modules by a case/whitespace-insensitive key; within each group,
    rewrites every case's `module` to the single exact spelling used by the
    MOST cases in that group, so the majority casing wins over a minority
    worker's variant. Never merges across genuinely different modules (the
    key is casefolded + whitespace-collapsed, not fuzzy). A no-op whenever
    every group already has just one exact spelling (the common case: nothing
    to fix). Never raises: any error returns cases unchanged.
    """
    try:
        if not cases:
            return cases
        buckets: dict[str, list[TestCase]] = {}
        for tc in cases:
            key = " ".join((tc.module or "").split()).casefold()
            buckets.setdefault(key, []).append(tc)
        needs_case_pass = not all(
            len({tc.module for tc in members}) <= 1 for members in buckets.values()
        )
        # The early return must NOT skip the qualifier pass: a suite can carry a
        # qualifier-prefixed variant with NO casing variant at all, and returning
        # here would leave it split (the 2026-08-03 bug this parameter fixes).
        if not needs_case_pass and not merge_qualifier_prefixes:
            return cases
        # Majority casing per key group: the single spelling used by the most
        # cases in that group wins over any minority variant sharing the key.
        canonical: dict[str, str] = {}
        if needs_case_pass:
            for key, members in buckets.items():
                counts: dict[str, int] = {}
                for tc in members:
                    counts[tc.module] = counts.get(tc.module, 0) + 1
                canonical[key] = max(counts.items(), key=lambda kv: kv[1])[0]
        changed = 0
        out: list[TestCase] = []
        for tc in cases:
            key = " ".join((tc.module or "").split()).casefold()
            target = canonical.get(key, tc.module)
            if target != tc.module:
                changed += 1
                out.append(tc.model_copy(update={"module": target}))
            else:
                out.append(tc)
        if changed:
            logger.info(
                "normalize_module_names: canonicalized %d case(s) across %d "
                "module name variant(s)",
                changed,
                sum(
                    1
                    for members in buckets.values()
                    if len({tc.module for tc in members}) > 1
                ),
            )
        if merge_qualifier_prefixes:
            label_counts: dict[str, int] = {}
            for tc in out:
                label_counts[tc.module] = label_counts.get(tc.module, 0) + 1
            merges = _qualifier_prefix_merges(label_counts)
            if merges:
                out = [
                    (
                        tc.model_copy(update={"module": merges[tc.module]})
                        if tc.module in merges
                        else tc
                    )
                    for tc in out
                ]
                logger.info(
                    "normalize_module_names: merged %d qualifier-prefixed label(s): %s",
                    len(merges),
                    "; ".join(f"{k!r} -> {v!r}" for k, v in sorted(merges.items())),
                )
        return out
    except Exception:
        logger.exception("normalize_module_names failed — returning cases unchanged")
        return cases


def data_notes_section(cases: list[TestCase]) -> str:
    """Build a one-line-per-case '## Test Data' markdown note for cases that declare
    a data plan, plus a BOUNDED one-line disclosure of the cases that declare NONE.
    At most DATA_NOTES_MAX_CASES case lines are enumerated, followed by a single
    overflow line pointing at the exported file — an uncapped list truncates the
    finalize reply.

    F01 (live run 2026-08-16, suite 1ed83399b4b84831b79ead7936235989): 19 of 96
    cases shipped with an empty ``test_data`` plan. That is CONTRACTUALLY LEGAL —
    agents.test_scenario_agent._TEST_DATA_INSTRUCTION tells the model to leave the
    array empty when the case manipulates no data, and TestCase.test_data is
    default_factory=list — so this is a DISCLOSURE, not a warning. Before the fix
    the section enumerated only the 77 cases that HAD a plan ("10 + 67 more"), so
    a reader doing arithmetic could not tell "needs no data" from "plan missing".

    The disclosure carries the affected case IDS -- not only a count -- whenever
    the missing cases are a MINORITY (below EMPTY_DATA_WARN_RATIO), and it is
    BUDGETED. F01 (2026-08-16) made it counts-only; SHYJ-5138 (2026-08-21)
    reversed that for the minority case, because "15 declare none" does not
    tell a tester to open TC-006 / TC-024 / TC-041. At or above the ratio the
    ids are suppressed and the line is byte-identical to its F01 form: the
    suite-level advisory already owns that message, and that shape has the
    least reply headroom of any (see the module constants above for the
    measurement that forced the gate).
    At most DATA_GAP_MAX_IDS ids are named, each clipped to
    DATA_GAP_MAX_ID_CHARS, followed by "(+N more)" -- so the line is bounded at
    ~190 chars at ANY suite size. The finalize reply is capped
    (tools/mcp_handlers._SUMMARY_CAP = 4000) and this section is followed by
    the anchoring / scope / grounding advisories, which are what a truncation
    actually cuts -- so the ids are paid for out of this section's own
    enumeration budget, and ONLY where that payment is real (see `limit`).
    tests/test_finalize_reply_cap.py measures all three shapes; measured
    end-to-end: 0/96 and 87/96 unchanged, 19/96 from 3938 to 3891 chars.

    Fires when at least one case declares no plan AND the suite carries at least
    EMPTY_DATA_MIN_CASES cases, reusing that existing threshold so small fixtures
    stay byte-identical. A suite where EVERY case declares a plan emits nothing
    new. A suite where NO case declares one is no longer silent: the old early
    ``return ""`` made the 100%-missing suite the least informative of all.

    Never raises.
    """
    try:
        rows: list[str] = []
        total = 0
        missing = 0
        missing_ids: list[str] = []
        for tc in cases:
            total += 1
            if not getattr(tc, "test_data", None):
                missing += 1
                missing_ids.append(str(getattr(tc, "tc_id", "") or "?"))
                continue
            fields = ", ".join(f"{it.field}={it.strategy}" for it in tc.test_data)
            rows.append(f"- {tc.tc_id}: {fields}")
        # Bounded gap line: counts always, plus the affected ids while they are
        # a MINORITY of the suite (see the module constants above for why the
        # ids were added and why the ratio gate is not optional). Rendered
        # AHEAD of the enumeration so an intra-section cut can never eat it.
        gap = ""
        if missing and total >= EMPTY_DATA_MIN_CASES:
            named = ""
            if missing < total * EMPTY_DATA_WARN_RATIO:
                shown_ids = [
                    str(i)[:DATA_GAP_MAX_ID_CHARS]
                    for i in missing_ids[:DATA_GAP_MAX_IDS]
                ]
                extra = missing - len(shown_ids)
                tail = f" (+{extra} more)" if extra > 0 else ""
                named = f": {', '.join(shown_ids)}{tail}"
            gap = (
                f"{total - missing} of {total} case(s) declare a data plan; "
                f"{missing} declare none (blank Test Data column){named}.\n"
            )
        if not rows:
            # Nothing to enumerate. Stay silent unless the gap line has something
            # to say, so an empty or sub-floor suite still renders nothing at all.
            return f"\n\n## Test Data\n{gap}".rstrip() if gap else ""
        header = (
            "\n\n## Test Data\n"
            f"{gap}"
            "Each case below declares what data it needs and how to source it "
            "(see the **Test Data** column in the exported file):\n"
        )
        # DATA_GAP_ROW_COST fewer enumerated rows when the gap line renders -- it
        # was ONE until SHYJ-5138 (2026-08-21) made the line name ids -- so the
        # disclosure is still paid for out of THIS section's budget instead of
        # out of the shared _SUMMARY_CAP. The dropped rows are not lost: they
        # roll into the overflow count, and the full plan is in the exported
        # file either way. The trade is deliberate on content as well as on
        # arithmetic: an enumerated row describes a case whose plan is ALREADY
        # in the exported Test Data column, whereas a named missing id is
        # actionable nowhere else.
        #
        # THE OVERFLOW CONDITION IS LOAD-BEARING. Charging the cost when the
        # enumeration fits today CREATES an "... and N more" line that was
        # absent, which costs ~87 chars to save ~150 of rows and can end up NET
        # POSITIVE -- measured at +42 on the 87-of-96 fixture in
        # tests/test_finalize_reply_cap.py, i.e. 4025 against a 4000 cap. So the
        # rows are only ever taken from an enumeration that already overflows,
        # where each dropped row is a pure saving. Never let this become an
        # unconditional `if gap`.
        limit = DATA_NOTES_MAX_CASES
        if gap and len(rows) > DATA_NOTES_MAX_CASES:
            limit = max(1, DATA_NOTES_MAX_CASES - DATA_GAP_ROW_COST)
        shown = rows[:limit]
        overflow = len(rows) - len(shown)
        if overflow:
            shown.append(
                f"- … and {overflow} more case(s) — the full plan is in the "
                "Test Data column of the exported file."
            )
        return header + "\n".join(shown)
    except Exception:
        logger.exception("test_data_notes_section failed — returning empty string")
        return ""
