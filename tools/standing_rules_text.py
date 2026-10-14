"""Assumption labels, markers and sanitisers for the standing-rules detector."""

from __future__ import annotations

import logging
import re

from config.settings_guard import never_raise

logger = logging.getLogger("tools.standing_rules")

# Mechanical assumption label. Fixed constants -- never model-written, and never
# citing a standard document the ticket did not name. The wording deliberately
# reads "we are guessing, verify the guess", NOT "documented in the REST
# standard, no need to check".
ASSUMED_EN = (
    "ASSUMPTION, NOT A REQUIREMENT: this behaviour is assumed based on standard "
    "REST convention and is NOT documented in {ref}. Verify the assumption with "
    "the API owner before signing the case off."
)
ASSUMED_AR = (
    "فرضية وليست متطلبًا: هذا السلوك مفترض بناءً على أعراف REST القياسية وغير "
    "موثّق في {ref}. يجب التحقق من الفرضية مع مالك الواجهة البرمجية قبل اعتماد "
    "الحالة."
)
DOCUMENTED_EN = "Contract taken from the OpenAPI/Swagger spec linked on {ref}."

# Marker the generator is told to put in the title of any case built on an
# assumed contract. Detected mechanically afterwards to attach the full label.
ASSUMED_MARKER = "[ASSUMED]"
CLARIFY_MARKER = "[NEEDS-CLARIFICATION]"

_MAX_REF_CHARS = 120
_MAX_QUESTION_CHARS = 200
_REF_STRIP_RE = re.compile(r"[\r\n\t]+")
# Leading markdown/quote/list structure an attacker-written comment could use to
# forge a heading, a list item or a blockquote inside OUR advisory section.
_MD_LEAD_RE = re.compile(r"^[\s>#*\-+0-9.)\]\[|`]+")


# ``standing_warning_section`` is the ONE place this batch echoes externally
# sourced text (an unresolved question lifted from the description or a Jira
# comment) back to the tester. It never reaches an LLM, so ``wrap_untrusted``
# is the wrong tool, but rendering it raw lets comment text forge markdown
# structure -- a fenced block, a heading, a link, or a bold line that reads like
# part of OUR report. Collapse whitespace, strip the leading list/heading/quote
# markers and neutralise the inline markdown/HTML metacharacters, then render
# the result inside a quoted span at the call site.
def safe_display(raw: str) -> str:
    """Neutralise a snippet of UNTRUSTED ticket prose for tester-facing markdown.

    Never raises; returns "" for empty input.
    """
    # defensive
    with never_raise():
        text = _REF_STRIP_RE.sub(" ", str(raw or "")).strip()
        text = _MD_LEAD_RE.sub("", text)
        return _neutralise(text)[:_MAX_QUESTION_CHARS]
    return ""


def _neutralise(text: str) -> str:
    """Blank the inline markdown/HTML metacharacters and collapse whitespace."""
    for ch in ("`", "*", "_", "[", "]", "<", ">", "|", "~"):
        text = text.replace(ch, " ")
    return " ".join(text.split())


def safe_ref(raw: str) -> str:
    """Sanitise a source reference (ticket key / URL) for display.

    Collapses newlines and caps the length so an externally-sourced value can
    never smuggle instructions or blow up a cell. Never raises.
    """
    # defensive
    with never_raise():
        s = _REF_STRIP_RE.sub(" ", str(raw or "")).strip()
        return (s[:_MAX_REF_CHARS] or "the source ticket").strip()
    return "the source ticket"


def assumed_label(source_ref: str, has_spec: bool, bilingual: bool = False) -> str:
    """The mechanical assumption/documentation label for an API case.

    Never raises.
    """
    # defensive
    with never_raise():
        ref = safe_ref(source_ref)
        if has_spec:
            return DOCUMENTED_EN.format(ref=ref)
        label = ASSUMED_EN.format(ref=ref)
        if bilingual:
            label = label + " | " + ASSUMED_AR.format(ref=ref)
        return label
    return ASSUMED_EN.format(ref="the source ticket")
