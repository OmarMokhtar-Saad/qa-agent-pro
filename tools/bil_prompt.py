"""Checklist lines and the system-prompt clause of the EN/AR bilingual rule pack."""

from __future__ import annotations

import logging

from tools.bil_pair import LanguagePair

logger = logging.getLogger("tools.bilingual")

_CHECKLIST_NOTE = (
    "Each one is already on the Atomic Requirements Checklist as a line "
    "with an id of the form RP-I18N-<KEY>. Cover the ones in YOUR "
    "category's scope; as with every other checklist line, coverage is "
    "recomputed afterwards by the same INDEPENDENT matcher, so your "
    "`requirement_id` tag can neither inflate nor deflate the score.\n"
)

_RULES = (
    "- Produce EXACTLY ONE test case per key, with TWO steps: step 1 with "
    "the application locale set to English, step 2 with the locale set to "
    "Arabic. NEVER split a key across two test cases.\n"
    "- NEVER retype, translate or paraphrase the message text. Write the "
    "placeholder token EXACTLY as shown and nothing else: the English "
    "expected result must contain {{EN:KEY}} and the Arabic expected "
    "result must contain {{AR:KEY}}, substituting the real key (e.g. "
    "{{EN:DM01}} / {{AR:DM01}}). Use DOUBLE braces. The system replaces "
    "the tokens with the documented strings after generation.\n"
    '- Quote the tokens, e.g. Expected: the banner shows EN: "{{EN:DM01}}" '
    'in English and AR: "{{AR:DM01}}" in Arabic.\n'
    "- Do NOT invent keys that are not in the list above.\n"
)


# The line carries the PLACEHOLDER TOKENS, never the literal strings, so the
# same text is safe on the checklist that is rendered into the prompt and into
# the XLSX 'Requirements Checklist' sheet. Written in EARS event_driven shape
# to match the rest of the Batch-2 checklist.
def checklist_lines(pairs: list[LanguagePair]) -> list[str]:
    """One mandatory checklist line per documented pair (decomposition rule)."""
    out: list[str] = []
    try:
        for p in pairs:
            out.append(
                f"When the {p.key} message is displayed, the system shall show it "
                "in BOTH documented languages, verified inside ONE test case: step "
                "1 with the application locale set to English, step 2 with the "
                "locale set to Arabic. The Expected Results shall contain, "
                f'literally: EN: "{{{{EN:{p.key}}}}}" and AR: "{{{{AR:{p.key}}}}}".'
            )
    except Exception:  # pragma: no cover - defensive
        logger.exception("checklist_lines failed - returning partial result")
    return out


def _head(pairs: list[LanguagePair], checklist_mode: bool) -> str:
    """Opening of the clause: the opaque keys, plus the checklist note."""
    keys = ", ".join(p.key for p in pairs)
    head = (
        "\n\n## EN/AR BILINGUAL RULE (mandatory)\n"
        f"The source ticket documents {len(pairs)} English/Arabic message "
        f"pair(s), keyed: {keys}.\n"
    )
    if checklist_mode:
        head += _CHECKLIST_NOTE
    return head


# Contains ONLY the opaque keys -- never the untrusted message text, which is
# what keeps this block outside the prompt-injection surface.
#
# ``checklist_mode`` is True when the mandated lines were injected onto the
# Batch-2 atomic checklist. The wording then points at the checklist ids and
# repeats Batch 2's "your requirement_id tag is advisory, coverage is
# recomputed externally" contract, so the two prompts cannot give the model
# contradictory instructions about who scores coverage.
def format_bilingual_prompt_block(
    pairs: list[LanguagePair], checklist_mode: bool = False
) -> str:
    """System-prompt clause for the EN/AR rule pack. "" when there are no pairs."""
    try:
        if not pairs:
            return ""
        return _head(pairs, checklist_mode) + _RULES
    except Exception:  # pragma: no cover - defensive
        logger.exception("format_bilingual_prompt_block failed - returning ''")
        return ""
