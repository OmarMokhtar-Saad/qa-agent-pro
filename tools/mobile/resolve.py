"""Resolve-or-ask: one exact answer, or a question -- never a guess.

Every mobile tool that needs an app, package, device, build flavour, release or
install source decides through :func:`resolve_or_ask`. The rule is the owner's
(2026-10-05): when there is not exactly ONE answer, stop and ask the tester.

* an explicit value always wins;
* a candidate whose key equals the text is exact;
* otherwise every candidate matching the text counts: one -> use it and say so,
  several -> ask, none -> say so and ask;
* a LABEL that equals the text is not exact: "acme" must not pick the
  production flavour when a qa and a master flavour are installed too.

The question travels through an injected :data:`AskCb` (the host builds it from
MCP elicitation), so this module imports nothing from ``tools.mcp_handlers``.
When the callback is missing, declined or raising, the caller gets the
unresolved :class:`Resolution` back and shows :meth:`Resolution.menu` instead.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Sequence

log = logging.getLogger(__name__)

RESOLVED = "resolved"
ASK = "ask"
NONE = "none"

#: Options shown in one question; the rest are summarised as "(+N more)". Each
#: option is a line the tester reads and the host model re-reads every turn.
MAX_ASK_OPTIONS = 8

#: ``(question, option_texts) -> the chosen option text, or None`` when nobody
#: answered (declined, timed out, client cannot elicit).
AskCb = Callable[[str, list], Awaitable[Optional[str]]]

#: What a failing ask transport may raise. A well-behaved AskCb returns None
#: instead (``_elicit_choice`` does); these are caught so a raising one still
#: degrades to the text menu rather than crashing the tool call.
ASK_FAILURES = (OSError, RuntimeError, TimeoutError, ValueError)

_IGNORED_WORDS = frozenset({"app", "apk", "application", "the", "my"})

_WHY = {
    "explicit": "you named it",
    "exact": "it matches exactly",
    "single": "it is the only match",
    "asked": "you chose it",
}


@dataclass(frozen=True)
class Candidate:
    """One thing the tester could mean: a package, a serial, a release."""

    key: str
    label: str = ""
    detail: str = ""

    def display(self) -> str:
        head = self.key
        if self.label and self.label != self.key:
            head = self.label + " (" + self.key + ")"
        return head + (" - " + self.detail if self.detail else "")


@dataclass(frozen=True)
class Resolution:
    """``status`` is RESOLVED (``value`` is the key), ASK or NONE (``question``
    and ``options`` say what to put to the tester)."""

    status: str
    value: Optional[str] = None
    options: tuple = ()
    question: str = ""
    how: str = ""

    @property
    def resolved(self) -> bool:
        return self.status == RESOLVED

    def said(self) -> str:
        """One sentence naming what was used and why, for the tool result."""
        why = _WHY.get(self.how, "")
        return "Using " + str(self.value) + (" (" + why + ")." if why else ".")

    def menu(self) -> str:
        """The text fallback when the client cannot elicit."""
        shown = self.options[:MAX_ASK_OPTIONS]
        lines = [self.question]
        lines += [str(n) + ". " + c.display() for n, c in enumerate(shown, 1)]
        if len(self.options) > len(shown):
            lines.append("(+" + str(len(self.options) - len(shown)) + " more)")
        lines.append("Ask the user which one they mean; do not guess.")
        return "\n".join(lines)


def _norm(text: object) -> str:
    return " ".join(str(text or "").lower().split())


def _words(text: object) -> list:
    found = re.findall(r"[a-z0-9]+", _norm(text))
    return [w for w in found if w not in _IGNORED_WORDS]


def fuzzy_match(text: str, cand: Candidate) -> bool:
    """Every meaningful word of ``text`` is a whole token of the key or label.

    Tokens split on anything not a letter or digit (``.``, ``_``, ``-``, space),
    so "qa" matches ``com.x.qa`` but never ``com.aqar.app``.
    """
    words = _words(text)
    tokens = set(re.findall(r"[a-z0-9]+", _norm(cand.key + " " + cand.label)))
    return bool(words) and all(w in tokens for w in words)


def exact_key_match(text: str, cand: Candidate) -> bool:
    """The whole key, case-insensitively: for serials, where a prefix is not a match."""
    return _norm(text) == _norm(cand.key)


def _asking(status: str, wanted: str, noun: str, options: tuple) -> Resolution:
    if status == NONE:
        head = "No " + noun + " matches " + repr(wanted) + "."
        head += (
            " These are available:" if options else " Ask the user for the exact name."
        )
    elif wanted:
        head = "More than one " + noun + " matches " + repr(wanted) + ". Which one?"
    else:
        head = "Which " + noun + "?"
    return Resolution(status, None, tuple(options), head)


def decide(
    text: str,
    candidates: Sequence[Candidate],
    *,
    noun: str = "app",
    explicit: Optional[str] = None,
    match: Callable[[str, Candidate], bool] = fuzzy_match,
) -> Resolution:
    """The pure decision. No I/O, no callback; see the module docstring."""
    pool = tuple(candidates)
    if explicit:
        return Resolution(RESOLVED, str(explicit), pool, how="explicit")
    wanted = _norm(text)
    exact = [c for c in pool if _norm(c.key) == wanted] if wanted else []
    if len(exact) == 1:
        return Resolution(RESOLVED, exact[0].key, (exact[0],), how="exact")
    hits = tuple(c for c in pool if match(text, c)) if wanted else pool
    if len(hits) == 1:
        return Resolution(RESOLVED, hits[0].key, hits, how="single")
    if hits:
        return _asking(ASK, wanted, noun, hits)
    return _asking(NONE, wanted, noun, pool)


def _pick(options: Sequence[Candidate], answer: Optional[str]) -> Optional[Candidate]:
    wanted = _norm(answer)
    if not wanted:
        return None
    for cand in options:
        if wanted in (_norm(cand.display()), _norm(cand.key)):
            return cand
    return None


async def resolve_or_ask(
    text: str,
    candidates: Sequence[Candidate],
    *,
    noun: str = "app",
    ask: Optional[AskCb] = None,
    match: Callable[[str, Candidate], bool] = fuzzy_match,
) -> Resolution:
    """Decide; when the decision is a question and ``ask`` exists, put it.

    An explicit value never needs asking: callers holding one call
    :func:`decide` with ``explicit=`` directly.

    Never raises for a failing transport: the unresolved Resolution comes back
    and its ``menu()`` is the text fallback.
    """
    first = decide(text, candidates, noun=noun, match=match)
    if first.resolved or ask is None or not first.options:
        return first
    shown = first.options[:MAX_ASK_OPTIONS]
    try:
        answer = await ask(first.question, [c.display() for c in shown])
    except ASK_FAILURES:  # a transport that cannot ask must degrade, not crash
        log.warning("resolve_or_ask: ask callback failed", exc_info=True)
        return first
    picked = _pick(shown, answer)
    if picked is None:
        return first
    return Resolution(RESOLVED, picked.key, (picked,), how="asked")
