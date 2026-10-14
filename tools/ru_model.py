"""Requirement-unit data model: RequirementUnit and DataFieldTable."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RequirementUnit:
    """One atomic, citable requirement.

    ``unit_id`` is what a generated test case puts in ``requirement_id``.
    ``provenance`` is ``"target"`` for the issue under test and ``"background"``
    for anything derived from parent / sibling / linked issues -- a case may
    only ever be anchored to a ``"target"`` unit.
    """

    unit_id: str
    text: str
    kind: str
    provenance: str = "target"

    @property
    def is_assignable(self) -> bool:
        """True when a test case may cite this unit as its requirement."""
        return self.provenance == "target"


@dataclass(frozen=True)
class DataFieldTable:
    """One DF table: its declared id and its rows.

    ``rows`` are (content_ar, content_en, type) triples as they appear. A table
    whose header could not be understood keeps whatever cells were present, so a
    malformed table degrades to fewer units rather than to an exception.
    """

    table_id: str
    rows: tuple[tuple[str, str, str], ...]

    def enum_values(self) -> set[str]:
        """Labels of the selectable (Checkbox / Radio) rows, in BOTH languages.

        Both columns count as defined options. Keeping only the English label
        meant an Arabic-valued case was reported as selecting "an option the
        ticket does not define" while that exact label sat in the ticket's own
        Arabic column on the same row -- and bilingual cases are the norm here
        (the bilingual rule pack, removed as a setting 2026-08-14), so that
        single omission would have fired on
        most real Arabic suites.
        """
        out: set[str] = set()
        for ar, en, kind in self.rows:
            if kind.lower().strip() in {"checkbox", "radio", "radio button"}:
                for raw in (en, ar):
                    label = _strip_placeholder_brackets(raw)
                    if label:
                        out.add(label)
        return out

    def allows_free_text(self) -> bool:
        """True when the table has a free-text row (an 'Other reason' escape)."""
        return any(
            k.lower().strip() in {"text", "textarea", "free text"}
            for _, _, k in self.rows
        )


def _strip_placeholder_brackets(text: str) -> str:
    """``<I no longer need the product>`` -> ``I no longer need the product``."""
    return (text or "").strip().strip("<>").strip()
