"""Data model for ``bound_consistency``: a finding and a parsed numeric bound."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BoundFinding:
    """One case step whose value contradicts a bound stated in the source."""

    tc_id: str
    step_number: int
    value: float
    subject: str
    low: float
    high: float
    kind: str  # "legal-rejected" | "illegal-accepted"


def _label_words(order: tuple[str, ...], distinctive: frozenset[str]) -> list[str]:
    words: list[str] = []
    for token in order:
        if token not in distinctive or token in words:
            continue
        if any("-" in seen and token in seen.split("-") for seen in words):
            continue
        words.append(token)
    return words


@dataclass(frozen=True)
class _Bound:
    low: float
    high: float
    increment: float | None
    subject: frozenset[str]
    distinctive: frozenset[str]
    # The same words as ``subject``, in the order the clause wrote them, so
    # the advisory can name the field rather than list its words.
    order: tuple[str, ...]

    # How the field is NAMED in the advisory -- distinctive words only,
    # in the order the SOURCE wrote them.
    #
    # Not the raw subject: that carries every content word before the range,
    # so the monthly-cap bound printed as "cap cardholder monthly spending"
    # and put a stray actor noun into tester-facing text. The distinctive
    # set is what identifies the field, and it is what the binding matched
    # on, so naming it keeps the report and the decision in step.
    #
    # Source order, not sorted: a tester reads "the monthly spending cap",
    # not "the cap monthly spending", and the label is the only part of this
    # finding that has to be recognised as a FIELD NAME rather than parsed.
    # The fragments a hyphenated token contributes are dropped as well --
    # they exist so "per transaction" matches "per-transaction" during
    # binding, and repeating them ("per-transaction transaction limit") only
    # makes the name harder to read. Falls back to the raw subject in the
    # degenerate case where nothing distinctive was left.
    @property
    def label(self) -> str:
        """How the field is NAMED in the advisory (see the comment above)."""
        distinctive = self.distinctive or self.subject
        words = _label_words(self.order, distinctive)
        return " ".join(words) if words else " ".join(sorted(distinctive))

    def is_legal(self, value: float) -> bool:
        if not self.low <= value <= self.high:
            return False
        return not (self.increment and value % self.increment)
