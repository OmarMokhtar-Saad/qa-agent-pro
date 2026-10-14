"""Templated manual (native-speaker) validation case."""

from __future__ import annotations

import logging

from tools.bil_pair import LanguagePair
from tools.models import AutomationStatus, Priority, TestCase, TestStep, TestType

logger = logging.getLogger("tools.bilingual")

_STEP1_HEAD = (
    "Switch the application locale to Arabic (Settings > Language > "
    "Arabic) and open every screen that shows a documented message: "
)

# (action, expected_result) of steps 2 and 3, then step 1's expected result
_STEP_TEXT = (
    (
        None,
        "Every documented Arabic string is rendered, right-aligned and "
        "fully visible (no truncation, no clipped diacritics) and is not "
        "replaced by its English fallback.",
    ),
    (
        "Have a NATIVE ARABIC SPEAKER read each rendered Arabic message "
        "and compare it word for word against the wording documented on "
        "the ticket.",
        "Each Arabic message matches the documented wording, is "
        "grammatically correct and reads naturally against its English "
        "counterpart. Any mismatch is raised as a defect on the ticket.",
    ),
    (
        "With the locale still Arabic, check numbers, dates, currency "
        "amounts and mixed-direction lines (a Latin product code inside "
        "an Arabic sentence).",
        "Numerals, dates and currency read in the correct order and "
        "mixed-direction lines are not visually reordered, split or "
        "mirrored.",
    ),
)


def _manual_validation_steps(keys: str, more: str) -> list[TestStep]:
    """The three fixed steps of the native-speaker validation case."""
    actions = (_STEP1_HEAD + f"{keys}{more}.",) + tuple(a for a, _ in _STEP_TEXT[1:])
    return [
        TestStep(step_number=i, action=a, test_data=None, expected_result=e)
        for i, (a, (_, e)) in enumerate(zip(actions, _STEP_TEXT), start=1)
    ]


def _manual_fields() -> dict:
    return {
        "module": "Localization (EN/AR)",
        "title": (
            "Native-speaker linguistic validation of every documented Arabic message"
        ),
        "priority": Priority.HIGH,
        "type": TestType.FUNCTIONAL,
        "preconditions": (
            "A build with the Arabic locale enabled and a native Arabic speaker "
            "available to review the wording."
        ),
        "postconditions": None,
        "automation_status": AutomationStatus.CANNOT_BE_AUTOMATED,
        "requirement_id": None,
    }


# A hand-authored, NON-generated linguistic-validation case.
#
# One automated bilingual case per key proves the strings are wired up; it
# cannot prove the Arabic is grammatical, natural, or laid out correctly.
# Industry practice pairs automated multi-locale checks with a native-speaker
# review pass, so the pack appends this fixed template (never LLM-written, so
# it cannot hallucinate) and marks it Cannot Be Automated. Lists KEYS only --
# no untrusted message text is embedded.
#
# Returns None when there are no pairs, or on any failure. Never raises.
def build_manual_validation_case(
    pairs: list[LanguagePair], tc_id: str = "TC-999"
) -> TestCase | None:
    """The fixed native-speaker validation case, or None."""
    try:
        if not pairs:
            return None
        keys = ", ".join(p.key for p in pairs[:30])
        more = " and the remaining documented keys" if len(pairs) > 30 else ""
        steps = _manual_validation_steps(keys, more)
        return TestCase(tc_id=tc_id, steps=steps, **_manual_fields())
    except Exception:
        logger.exception("build_manual_validation_case failed - returning None")
        return None
