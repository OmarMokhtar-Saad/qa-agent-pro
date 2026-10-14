"""Roles and readability: what an element is for, and what the tokeniser cannot see."""

from __future__ import annotations

import re

from tools.mobile.perception_consts import (
    ROLE_INPUT,
    ROLE_LEXICON,
    ROLE_PASSWORD,
    SEGMENT_BREAK,
)
from tools.mobile.perception_label import _own_label, label_of
from tools.mobile.perception_text import split_camel, words


def with_camel_split(text: object) -> str:
    """*text* followed by its ``split_camel`` form, when the two differ.

    For the destructive guard on ids. Splitting alone loses lexicon words that
    are ONE token: ``CheckOut`` split is ``Check Out``, and ``check out`` is not
    ``checkout``. Judging both forms keeps every word the raw id matched and
    adds the words the split exposes (``signOut`` -> ``sign out``). The two are
    joined by ``SEGMENT_BREAK``, never a space, so no term spans the seam.
    """
    raw = str(text or "")
    split = split_camel(raw)
    return raw if split == raw else raw + SEGMENT_BREAK + split


#: Anything alphabetic that :func:`words` cannot see. The tokeniser is ASCII on
#: purpose -- the role lexicon and the destructive lexicon are English words an
#: Android build emits -- but that makes it BLIND rather than permissive, and a
#: caller that treats "no token matched" as "nothing to worry about" has built a
#: security control that cannot fail closed.
_UNREADABLE_RE = re.compile(r"[^\x00-\x7f]")


def has_unreadable_text(*values: object) -> bool:
    """True when any of *values* holds a character :func:`words` cannot tokenise.

    The distinction this exists to draw: "this name matched nothing in my list"
    and "this name is not in an alphabet I can read" are different answers, and
    only the first is evidence. A caller deciding whether to PRINT something
    must treat the second as a refusal.
    """
    for value in values:
        if _UNREADABLE_RE.search(str(value or "")):
            return True
    return False


def _role_from_tokens(tokens: set[str]) -> str:
    """The first lexicon role whose terms intersect *tokens*, else ``""``."""
    for role, terms in ROLE_LEXICON:
        if tokens.intersection(terms):
            return role
    return ""


# ``input``/``send``/``voice``/``back``/``close``, or ``""``. Never guesses.
#
# Matched as WHOLE WORDS over the label and the resource-id, so ``Compass``
# is not a ``send`` and ``Recording saved`` is a ``voice`` only because
# ``record``'s own word is not in it -- ``recording`` is a different token.
# That strictness is deliberate: a wrong role is worse than none.
def role_of(element: object, label: str = "") -> str:
    """``input``/``send``/``voice``/``back``/``close``, or ``""``. Never guesses."""
    if not isinstance(element, dict):
        return ""
    if element.get("secure"):
        return ROLE_PASSWORD
    if element.get("editable"):
        return ROLE_INPUT
    rid = str(element.get("rid") or "").rsplit("/", 1)[-1]
    return _role_from_tokens(set(words(label or _own_label(element), rid)))


def annotate(content: object) -> object:
    """Give every element in a pruned screen its ``label`` and its ``role``.

    Separated from :func:`prune` so it can be exercised against a screen read
    back from a run's own ``screens/`` directory -- which is how the 2026-09-04
    chat screen became a fixture in this repository rather than an anecdote.
    Idempotent.
    """
    body = content if isinstance(content, dict) else {}
    elements = [e for e in (body.get("elements") or []) if isinstance(e, dict)]
    for element in elements:
        label = label_of(element, elements)
        element["label"] = label
        element["role"] = role_of(element, label)
    return content
