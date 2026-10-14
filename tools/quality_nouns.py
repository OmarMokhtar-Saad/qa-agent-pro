"""Part of the quality-check split (see tools/quality_checks.py): nouns."""

from __future__ import annotations

import re

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

# The nouns this detector knows by name. A word here is a HEAD wherever it
# stands, even when it also looks like something else -- "setting" and "settings"
# end in "ing" and are not participles, and that collision is why the participle
# test below consults this set FIRST rather than pattern-matching in isolation.
_KNOWN_NOUNS = _NON_PLACE_HEADS | _GENERIC_CONTAINER_NOUNS
