"""The LanguagePair value class of the bilingual rule pack."""

from __future__ import annotations

from typing import NamedTuple

from tools import bil_key


class PairSpec(NamedTuple):
    """The raw fields of one documented pair (a parameter object)."""

    key: str
    en: str
    ar: str
    source_line: str = ""


class LanguagePair:
    """One documented English/Arabic message pair from the ticket.

    A plain class (not a dataclass) so ``key`` is normalised on construction
    without a __post_init__ dance; every caller treats instances as immutable.
    """

    __slots__ = ("key", "en", "ar", "source_line")

    def __init__(self, spec: PairSpec) -> None:
        self.key = bil_key.normalize_key(spec.key)
        self.en = spec.en
        self.ar = spec.ar
        self.source_line = spec.source_line

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"LanguagePair(key={self.key!r}, en={self.en!r}, ar={self.ar!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LanguagePair):
            return NotImplemented
        return (self.key, self.en, self.ar) == (other.key, other.en, other.ar)

    def __hash__(self) -> int:
        return hash((self.key, self.en, self.ar))
