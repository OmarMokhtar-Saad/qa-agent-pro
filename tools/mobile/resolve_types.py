"""Types and constants of the resolve-or-ask decision (leaf of resolve)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

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
ASK_FAILURES = (
    OSError,
    RuntimeError,
    TimeoutError,
    ValueError,
    LookupError,
    TypeError,
    AttributeError,
)

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
