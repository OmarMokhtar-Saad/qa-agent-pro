"""A pydantic ValidationError in words a manual tester can act on."""

from __future__ import annotations

from config.settings_guard import never_raise
from tools.untrusted import single_line

# Tester-facing name and source column for each model field a row can violate.
# Read off ``tools/models.py`` rather than guessed: BOTH ``TestStep.action`` and
# ``TestStep.expected_result`` carry ``min_length=5``, ``TestCase.title`` is
# 10..250 and ``TestCase.module`` 2..100. That contract is shared with the whole
# generation pipeline and is NOT relaxed here -- a row that cannot satisfy it is
# refused by name, never padded.
_FIELD_LABELS: dict[str, tuple[str, str]] = {
    "expected_result": ("expected result", "Expected Results"),
    "action": ("step action", "Steps / Actions"),
    "step_number": ("step number", "Steps / Actions"),
    "title": ("title", "Title"),
    "module": ("module", "Module"),
    "tc_id": ("case id", "TC ID"),
    "priority": ("priority", "Priority"),
    "type": ("test type", "Test Type"),
}

# kind -> (text before the limit, ctx key, text after the limit)
_LENGTH = {
    "string_too_short": (" needs at least ", "min_length", " characters"),
    "string_too_long": (" is longer than ", "max_length", " characters"),
}
_FIXED = {
    "string_pattern_mismatch": " is not in the expected format",
    "missing": " is missing",
    "too_short": " has no entries",
}


def _fallback(exc: BaseException | None) -> str:
    return str(exc).splitlines()[0][:200]


def _first_problem(exc: BaseException | None) -> dict | None:
    reporter = getattr(exc, "errors", None)
    if not callable(reporter):
        return None
    with never_raise() as g:
        found = list(reporter())
    if g.failed:
        return None
    return found[0] if found else None


def _subject(problem: dict) -> tuple[str, str]:
    """The ``(subject, column)`` words for the field a problem is about."""
    location = list(problem.get("loc") or ())
    field = next((str(p) for p in reversed(location) if isinstance(p, str)), "")
    step_index = next((int(p) for p in location if isinstance(p, int)), -1)
    label, column = _FIELD_LABELS.get(field, (field or "value", ""))
    where = "step " + str(step_index + 1) + "'s " if step_index >= 0 else ""
    return where + label, column


# Before this existed a rejected row read ``1 validation error for TestStep``
# -- technically true and useless, because it named neither the column, nor
# the step, nor the rule. That broke this module's own promise that a bad row
# is reported WITH ITS REASON: a tester whose table said ``Expected: OK`` was
# told only that something, somewhere, was invalid.
#
# The rule itself is not relaxed and the value is not padded. An expected
# result really does need five characters, and inventing one to get past the
# validator would manufacture an assertion the tester never wrote.
def _explain_validation(exc: BaseException | None) -> str:
    """A pydantic ValidationError in words a manual tester can act on."""
    fallback = _fallback(exc)
    problem = _first_problem(exc)
    if problem is None:
        return fallback
    subject, column = _subject(problem)
    detail = _problem_detail(problem, subject)
    if column:
        detail += " (column: " + column + ")"
    return detail


def _problem_detail(problem: dict, subject: str) -> str:
    """The sentence for one pydantic error kind, about *subject*."""
    context = problem.get("ctx") or {}
    kind = str(problem.get("type") or "")
    if kind in _LENGTH:
        before, key, after = _LENGTH[kind]
        return subject + before + str(context.get(key)) + after
    if kind in _FIXED:
        return subject + _FIXED[kind]
    return subject + ": " + single_line(problem.get("msg") or "", 120)
