"""Host mode: per-case salvage and the case-list validation loop."""

from __future__ import annotations

from agents.host_prep_serde import PrepParseError
from agents.host_submission_detail import _validation_detail
from agents.host_submission_dropped import _all_dropped_error
from config.settings_guard import never_raise
from tools.models import TestCase


def _salvage_case(c) -> tuple:
    """Validate ONE submitted case: ``(tc, dropped_reason, salvaged_reason)``.

    Exactly one of ``tc`` / ``dropped_reason`` is set; ``salvaged_reason`` is set
    only when the case was kept without its ``test_data`` plan.
    """
    if not isinstance(c, dict):
        return None, "a non-object entry in test_cases", None
    with never_raise() as g:
        return TestCase(**c), None, None
    assert isinstance(g.exc, Exception)
    return _salvage_failed_case(c, g.exc)


# 2026-08-31 (F9): `test_data` is an OPTIONAL per-case provisioning
# plan, and ONE wrong key inside it ("example" for `example_value`,
# "seed account" for `seed_account`) used to discard the whole case.
# Measured: 4 of 9 cases lost, the category then accepted at 5 with
# a success-shaped reply. TestDataItem's own docstring justifies the
# strict enum by "the per-category retry regenerates it" -- there is
# no retry on this path. Salvage the CASE, drop only the plan, and
# say so.
def _case_without_test_data(c: dict) -> TestCase | None:
    if isinstance(c.get("test_data"), list) and c.get("test_data"):
        with never_raise():
            return TestCase(**{k: v for k, v in c.items() if k != "test_data"})
        return None
    return None


# F15 (2026-08-30): the class name alone ("failed validation
# (ValidationError)") does not say WHICH field or WHICH rule,
# so a host cannot fix the case without re-deriving the schema.
# Name up to two field/message pairs; the messages are
# pydantic's own text, never the rejected VALUE, so nothing
# untrusted is echoed back.
def _salvage_failed_case(c: dict, exc: Exception) -> tuple:
    raw_id = c.get("tc_id")
    tcid = raw_id if isinstance(raw_id, str) else "?"
    tc = _case_without_test_data(c)
    if tc is None:
        return None, f"{tcid}: failed validation ({_validation_detail(exc)})", None
    salvaged_reason = (
        f"{tcid}: kept, but its `test_data` plan was dropped "
        f"({_validation_detail(exc)})"
    )
    return tc, None, salvaged_reason


def _require_case_list(cases) -> None:
    if not isinstance(cases, list):
        raise PrepParseError("submitted suite has no 'test_cases' list")


def _keep_unique(results: list) -> tuple[list, list]:
    valid: list[TestCase] = []
    seen_ids: set[str] = set()
    dropped: list[str] = []
    for tc, dropped_reason, _ in results:
        if tc is None:
            dropped.append(dropped_reason)
        elif tc.tc_id in seen_ids:
            dropped.append(f"{tc.tc_id}: duplicate tc_id")
        else:
            seen_ids.add(tc.tc_id)
            valid.append(tc)
    return valid, dropped


def _validate_cases(cases) -> tuple[list, list, list]:
    """The salvage loop: ``(valid, dropped, salvaged)``, in submission order.

    Keeps every individually-valid case, drops malformed ones and any repeated
    tc_id, and records each drop. Raises PrepParseError when nothing is valid.
    """
    _require_case_list(cases)
    results = [_salvage_case(c) for c in cases]
    salvaged = [s for _, _, s in results if s]
    valid, dropped = _keep_unique(results)
    if not valid:
        raise _all_dropped_error(dropped)
    return valid, dropped, salvaged
