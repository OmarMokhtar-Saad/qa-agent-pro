"""Advisory markdown section for the standing rules."""

from __future__ import annotations

import logging

from tools.standing_rules_detect import Triggers
from tools.standing_rules_text import (
    ASSUMED_MARKER,
    CLARIFY_MARKER,
    assumed_label,
    safe_display,
)

logger = logging.getLogger("tools.standing_rules")


def _weak_only_row(api_evidence: list[str]) -> str:
    """The row warning that the API trigger fired on weak signals only."""
    return (
        "  - ⚠️ **The trigger was CIRCUMSTANTIAL.** No word like "
        '"API", "endpoint" or "status code" appears on this ticket; the '
        "rules fired on "
        + ", ".join(f"`{e}`" for e in api_evidence[:5])
        + ". If this story has no backend surface, ignore the "
        "API cases -- they are advisory."
    )


def _api_head_rows(triggers: Triggers) -> list[str]:
    """The "API rules fired" row plus the circumstantial-trigger row."""
    src = (
        "the linked OpenAPI/Swagger spec"
        if triggers.has_spec
        else "standard REST convention"
    )
    lines = [
        "- **API rules fired** (matched: "
        + ", ".join(triggers.api_evidence[:5] or ["an OpenAPI spec"])
        + ") - status-code, request-design and response-structure cases are "
        f"mandatory and were written against {src}."
    ]
    if triggers.api_weak_only:
        lines.append(_weak_only_row(triggers.api_evidence))
    return lines


def _no_contract_row(source_ref: str) -> str:
    """The row explaining the Notes-column label of the assumed cases."""
    return (
        "  - No contract is documented on the ticket. Cases marked "
        f"`{ASSUMED_MARKER}` carry this note in the Excel **Notes** "
        f"column: _{assumed_label(source_ref, False)}_"
    )


def _spec_hint_row(spec_hint: str) -> str:
    """The row pointing at a spec URL found in the ticket text."""
    return (
        "  - A spec URL appears in the ticket text "
        f"(`{spec_hint}`). Spec ingestion is always on, so "
        "if the API cases were not written from the contract the "
        "URL was not recognised as an OpenAPI/Swagger link, or the "
        "fetch failed."
    )


def _api_warning_rows(triggers: Triggers, source_ref: str) -> list[str]:
    """The advisory rows for a fired API trigger."""
    lines = _api_head_rows(triggers)
    if not triggers.has_spec:
        lines.append(_no_contract_row(source_ref))
    if triggers.spec_hint:
        lines.append(_spec_hint_row(triggers.spec_hint))
    return lines


def _question_warning_rows(open_questions: list[str]) -> list[str]:
    """The advisory rows listing unresolved ticket questions."""
    lines = [
        "- **Unresolved questions found in the ticket / comments - these were "
        "NOT answered by the generator:**"
    ]
    for q in open_questions[:5]:
        # UNTRUSTED, attacker-writable ticket/comment prose. safe_display
        # strips the markdown structure it could otherwise forge inside
        # this section, and the quotes make the provenance obvious.
        safe = safe_display(q)
        if safe:
            lines.append(f'  - "{safe}"')
    lines.append(f"  Resolve them before executing any case marked `{CLARIFY_MARKER}`.")
    return lines


def _notes_row(notes: dict[str, str]) -> str:
    """The row listing the cases that carry an assumption / clarification note."""
    return (
        "- Cases carrying an assumption / clarification note: "
        + ", ".join(sorted(notes)[:20])
        + (" ..." if len(notes) > 20 else "")
    )


def _warning_rows(
    triggers: Triggers, notes: dict[str, str], source_ref: str
) -> list[str]:
    """The advisory bullet rows under the standing-rules heading."""
    lines: list[str] = []
    if triggers.api:
        lines.extend(_api_warning_rows(triggers, source_ref))
    if triggers.ui:
        lines.append(
            "- **User-facing screen detected** - a baseline UI build-quality case "
            "is mandatory (elements present, spelling, alignment, truncation, "
            "interactive states, smallest supported width)."
        )
    if triggers.open_questions:
        lines.extend(_question_warning_rows(triggers.open_questions))
    if notes:
        lines.append(_notes_row(notes))
    return lines


def standing_warning_section(
    triggers: Triggers, notes: dict[str, str], source_ref: str = ""
) -> str:
    """Advisory markdown for the standing rules. "" when nothing fired.

    Never raises.
    """
    try:
        if not triggers.fired and not triggers.open_questions:
            return ""
        lines = ["\n\n## Standing Rules (advisory)", ""]
        lines.extend(_warning_rows(triggers, notes, source_ref))
        return "\n".join(lines)
    except Exception:
        logger.exception("standing_warning_section failed - returning ''")
        return ""
