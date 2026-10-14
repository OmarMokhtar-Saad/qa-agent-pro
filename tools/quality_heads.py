"""Part of the quality-check split (see tools/quality_checks.py): heads."""

from __future__ import annotations

import re

from tools.quality_nouns import (
    _GENERIC_CONTAINER_NOUNS,
    _KNOWN_NOUNS,
    _NAMED_QUALIFIER,
    _NON_PLACE_HEADS,
)
from tools.quality_phrase_end import _NP_BREAK


def _bare(word: str) -> str:
    """A phrase token stripped of quotes and lowercased, for set lookups."""
    return word.strip("'\u2019\"").lower()


# A token that cannot be the head of a noun phrase: a function word that opens a
# new phrase, the two compound-builders, or a participle. Derived from _NP_BREAK
# rather than restated -- a boundary word and a non-head are the same policy
# question asked twice, and this plan's whole history is what happens when two
# code paths answer it separately.
_NOT_A_HEAD = frozenset(_NP_BREAK.split("|")) | {"of", "to"}

_PARTICIPLE = re.compile(r"[\w-]*ing", re.IGNORECASE)


# _head_is_a_place used to ask only "is this word in a not-a-place set", which
# assumed the last word was a noun at all. Round 7's F1: "Enter 'abc' in the
# amount field TO save it" captured "amount field to" and answered on "to";
# "in the monthly cap field EXCEEDING the limit" answered on a participle.
# A QUANTITY is not a head noun ("on debit 4521" is about the card), so digits
# reject the word (round 8, revision 9: "toolbar 2 field" must judge on
# "field"). A URL has exactly ONE judge, _url_names_a_destination, because
# deciding it needs what the step DOES with it; revision 8's audit caught the
# nav path judging "https" as a head ("Open https://evil... in the redirect
# link field" was DATA to the URL rule and a PLACE to the nav rule), so URLs
# are excluded HERE, in the shared test, not at one call site.
def _is_a_head(word: str) -> bool:
    """True when `word` can be the HEAD NOUN of a phrase."""
    if not word:
        return False
    if word in _KNOWN_NOUNS:
        return True
    if word in _NOT_A_HEAD:
        return False
    if any(ch.isdigit() for ch in word):
        return False
    if word in ("http", "https") or word.startswith(("http://", "https://")):
        return False
    return not _PARTICIPLE.fullmatch(word)


# The QUALIFIER has to carry the name, never the generic head itself: "In the
# Section, tap Freeze" is capitalisation, not a name. A generic container cannot
# be named by ANOTHER generic container, which is what let "In the Settings
# panel" anchor while "Navigate to Settings" stayed weak (round 6's F4).
def _qualified_container(words: list[str]) -> bool:
    """True when a generic-container head carries a named qualifier."""
    qualifier = " ".join(
        w for w in words[:-1] if _bare(w) not in _GENERIC_CONTAINER_NOUNS
    )
    return bool(_NAMED_QUALIFIER.search(qualifier))


# A noun phrase names a place unless its HEAD -- the last word -- is a control
# ("the account number FIELD"), a device ("on IPHONE"), a duration ("within 60
# SECONDS"), a bare number ("on debit 4521") or an UNNAMED generic container
# ("in the monthly cap SECTION"). One function on purpose: revision 5 applied
# these exclusions in the locative path only, and the nav verb and "from
# <place>" predicates silently disagreed with it ("Open the 'Per-transaction
# limit' field" anchored on the quoted name). A shared test is the only shape in
# which those paths cannot drift apart again. A trailing function word or
# participle is trimmed back first ("amount field to" is a field; round 7's F1).
def _head_is_a_place(phrase: str) -> bool:
    """The ONE head-noun test. Every place predicate goes through it."""
    words = phrase.split()
    while words and not _is_a_head(_bare(words[-1])):
        words = words[:-1]
    if not words:
        return False
    head = _bare(words[-1])
    if not head:
        return False
    if head in _GENERIC_CONTAINER_NOUNS:
        return _qualified_container(words)
    return head not in _NON_PLACE_HEADS


def _any_phrase_is_a_place(pattern, text: str) -> bool:
    """True when ANY phrase this pattern captures has a place for a head."""
    return any(
        _head_is_a_place((m.group(1) or "").strip()) for m in pattern.finditer(text)
    )
