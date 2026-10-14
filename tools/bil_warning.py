"""Advisory markdown section for the EN/AR bilingual pack."""

from __future__ import annotations

import logging

from tools.bil_pair import LanguagePair

logger = logging.getLogger("tools.bilingual")


# (report key, bullet text, True when the list gets a " ..." overflow marker)
_WARNING_SPECS: tuple[tuple[str, str, bool], ...] = (
    ("missing_keys", "- **No test case covers these keys:** ", True),
    (
        "partial_keys",
        "- **Only ONE language was quoted (the pair is incomplete):** ",
        False,
    ),
    (
        "split_keys",
        "- **EN and AR landed in DIFFERENT test cases (should be one case, "
        "two steps):** ",
        False,
    ),
    (
        "baked_keys",
        "- **The model typed the string itself instead of using the "
        "placeholder - re-check it character by character against the "
        "ticket:** ",
        False,
    ),
    (
        "unresolved",
        "- **Placeholders referenced a key the ticket does not document:** ",
        False,
    ),
    (
        "residual_tokens",
        "- **Unresolved placeholder tokens were neutralised in these cases - "
        "they now read the 'not documented' text instead of a message:** ",
        False,
    ),
)


def _warning_findings(report: dict) -> list[str]:
    """One bullet per non-empty problem list in the coverage report."""
    findings: list[str] = []
    for report_key, text, marks_overflow in _WARNING_SPECS:
        keys = report.get(report_key) or []
        if not keys:
            continue
        overflow = " ..." if marks_overflow and len(keys) > 20 else ""
        findings.append(text + ", ".join(keys[:20]) + overflow)
    return findings


def _intro(pairs: list[LanguagePair], report: dict) -> list[str]:
    return [
        "\n\n## EN/AR Bilingual Coverage",
        "",
        f"The ticket documents **{len(pairs)}** English/Arabic message pair(s). "
        f"**{len(report.get('covered_keys') or [])}** were carried into the suite "
        "verbatim (substituted in code, not retyped by the model).",
        "",
        "> AUTOMATED coverage only. One bilingual case per key proves the strings "
        "are wired up; it does NOT prove the Arabic is grammatical, natural or "
        "correctly laid out. The templated _Native-speaker linguistic "
        "validation_ case in this suite is the MANUAL half and must be executed "
        "by an Arabic speaker.",
    ]


# Advisory markdown for the EN/AR pack. "" when there is nothing to report.
#
# Never raises.
def bilingual_warning_section(pairs: list[LanguagePair], report: dict) -> str:
    """Advisory markdown for the EN/AR pack. "" when there is nothing to report."""
    try:
        if not pairs:
            return ""
        report = report or {}
        lines = _intro(pairs, report)
        findings = _warning_findings(report)
        lines.extend(findings)
        if not findings:
            lines.append("- All documented pairs are covered in a single case each.")
        return "\n".join(lines)
    except Exception:
        logger.exception("bilingual_warning_section failed - returning ''")
        return ""
