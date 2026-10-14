"""System-prompt clause for the standing rules."""

from __future__ import annotations

import logging

from tools.standing_rules_checklist import standing_checklist_lines
from tools.standing_rules_detect import Triggers
from tools.standing_rules_text import ASSUMED_MARKER, CLARIFY_MARKER

logger = logging.getLogger("tools.standing_rules")


def _prompt_block_head(checklist_mode: bool) -> str:
    """The opening of the standing-rules prompt clause."""
    if checklist_mode:
        return (
            "\n\n## STANDING RULES (mandatory)\n"
            "The ticket content triggered the rules below. They are ALREADY on "
            "the Atomic Requirements Checklist as lines whose ids start with "
            "RP-SR-. Cover the ones in YOUR category's scope; coverage is "
            "recomputed afterwards by the same INDEPENDENT matcher used for "
            "every other checklist line, so your `requirement_id` tag is "
            "advisory:\n"
        )
    return (
        "\n\n## STANDING RULES (mandatory)\n"
        "The ticket content triggered the rules below. Produce at least one "
        "dedicated test case for each bullet that falls in your category's "
        "scope:\n"
    )


def _no_spec_tail(triggers: Triggers) -> str:
    """The API-contract note when the ticket documents no contract."""
    tail = (
        "\n- The ticket documents NO API contract. Write the API cases "
        "against standard REST convention and put the literal marker "
        f"{ASSUMED_MARKER} at the START of each such case's title. Do NOT "
        "cite any RFC, standard number or external document - the system "
        "attaches the assumption label mechanically.\n"
    )
    if triggers.api_weak_only:
        tail += (
            "- The API signals in this ticket are CIRCUMSTANTIAL (matched: "
            + ", ".join(triggers.api_evidence[:5])
            + "). If the story genuinely has no API surface, keep these "
            "cases to ONE minimal case per bullet rather than expanding "
            "them.\n"
        )
    return tail


def _api_tail(triggers: Triggers) -> str:
    """The API-contract note of the prompt clause ("" when no API trigger)."""
    if triggers.api and not triggers.has_spec:
        return _no_spec_tail(triggers)
    if triggers.api and triggers.has_spec:
        return (
            "\n- An OpenAPI/Swagger spec was supplied above: take field names, "
            "types and status codes from it and do NOT mark those cases "
            f"{ASSUMED_MARKER}.\n"
        )
    return ""


def _question_tail() -> str:
    """The unresolved-question note of the prompt clause."""
    return (
        "\n- The ticket contains UNRESOLVED questions. Do NOT silently pick "
        "an answer. If a case depends on one, write the case for the most "
        "conservative reading and start its title with the literal marker "
        f"{CLARIFY_MARKER}.\n"
    )


def _prompt_block_tail(triggers: Triggers) -> str:
    """The API-contract and open-question notes closing the prompt clause."""
    tail = _api_tail(triggers)
    if triggers.open_questions:
        tail += _question_tail()
    return tail


def _prompt_block_lines(
    triggers: Triggers, lines: list[tuple[str, str, str]], checklist_mode: bool
) -> str:
    """The head + checklist bullets + tail of the standing-rules prompt clause."""
    body = "\n".join(f"- [{lid}] {text}" for lid, text, _sub in lines)
    return _prompt_block_head(checklist_mode) + body + _prompt_block_tail(triggers)


def _unresolved_only_block() -> str:
    """The clause for a ticket with no fired rule but unresolved questions."""
    return (
        "\n\n## UNRESOLVED QUESTIONS IN THE TICKET\n"
        "The ticket contains questions nobody has answered. Do NOT silently "
        "pick an answer. If a case depends on one, write the case for the "
        "most conservative reading and start its title with the literal "
        f"marker {CLARIFY_MARKER}.\n"
    )


# Only the ticket REFERENCE (sanitised) and code constants reach the prompt --
# no untrusted ticket body.
#
# ``checklist_mode`` is True when the mandated lines were injected onto the
# Batch-2 atomic checklist. The wording then names the checklist ids and
# repeats Batch 2's "coverage is recomputed externally" contract instead of
# telling the model to produce cases "in addition to your category's normal
# output", which would have contradicted it. Never raises.
def format_standing_prompt_block(
    triggers: Triggers, source_ref: str = "", checklist_mode: bool = False
) -> str:
    """System-prompt clause for the standing rules. "" when nothing fired."""
    try:
        lines = standing_checklist_lines(triggers)
        if not lines and not triggers.open_questions:
            return ""
        if not lines:
            # No standing rule fired, but the ticket has UNRESOLVED questions. The
            # clarification instruction is the whole point of detecting them, so it
            # must still reach the model instead of being dropped with the block.
            return _unresolved_only_block()
        return _prompt_block_lines(triggers, lines, checklist_mode)
    except Exception:
        logger.exception("format_standing_prompt_block failed - returning ''")
        return ""
