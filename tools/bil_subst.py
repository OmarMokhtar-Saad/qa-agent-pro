"""Deterministic ``{{EN:KEY}}`` / ``{{AR:KEY}}`` substitution into generated cases."""

from __future__ import annotations

import logging
import re

from pydantic import ValidationError

from tools import bil_key, bil_tokens
from tools.bil_pair import LanguagePair
from tools.bil_report import CoverageInputs, _fill_coverage_report, new_report
from tools.bil_tokens import PLACEHOLDER_RE, UNRESOLVED_TEXT
from tools.models import TestCase

logger = logging.getLogger("tools.bilingual")


def _lookup(pairs: list[LanguagePair]) -> dict[str, LanguagePair]:
    table: dict[str, LanguagePair] = {}
    for p in pairs:
        k = bil_key.normalize_key(p.key)
        if k:
            table[k] = p
    return table


def _sub_text(
    text: str,
    table: dict[str, LanguagePair],
    sinks: tuple[set[str], set[str]],
) -> str:
    used, unresolved = sinks

    def _repl(m: re.Match) -> str:
        lang = m.group(1).upper()
        key = bil_key.normalize_key(m.group(2))
        pair = table.get(key)
        if pair is None:
            unresolved.add(f"{lang}:{key}")
            return UNRESOLVED_TEXT
        used.add(f"{lang}:{key}")
        return pair.en if lang == "EN" else pair.ar

    return PLACEHOLDER_RE.sub(_repl, text or "")


def _sub_top(data: dict, table: dict, sinks: tuple[set[str], set[str]]) -> bool:
    touched = False
    for fname in ("title", "preconditions", "postconditions", "module"):
        value = data.get(fname)
        if isinstance(value, str) and PLACEHOLDER_RE.search(value):
            data[fname] = bil_tokens._clamp(_sub_text(value, table, sinks), fname)
            touched = True
    return touched


def _sub_steps(data: dict, table: dict, sinks: tuple[set[str], set[str]]) -> bool:
    touched = False
    for step in data.get("steps") or []:
        for fname in ("action", "test_data", "expected_result"):
            value = step.get(fname)
            if isinstance(value, str) and PLACEHOLDER_RE.search(value):
                step[fname] = _sub_text(value, table, sinks)
                touched = True
    return touched


def _revalidate(data: dict, tc: TestCase) -> TestCase | None:
    try:
        return TestCase.model_validate(data)
    except ValidationError:
        logger.warning(
            "bilingual substitution produced an invalid case (%s) - keeping "
            "the original; the residual-token sweep will neutralise any "
            "token left in it",
            getattr(tc, "tc_id", "?"),
            exc_info=True,
        )
        return None


# Substitute one case. Returns ``(case, used, unresolved, rebuilt)``;
# ``rebuilt`` is False when the case was untouched or re-validation failed
# (the original is kept).
def _substitute_case(
    tc: TestCase, table: dict
) -> tuple[TestCase, set[str], set[str], bool]:
    """Substitute one case."""
    used: set[str] = set()
    local_unresolved: set[str] = set()
    data = tc.model_dump()
    sinks = (used, local_unresolved)
    top = _sub_top(data, table, sinks)
    steps = _sub_steps(data, table, sinks)
    if not (top or steps):
        return tc, used, local_unresolved, False
    case = _revalidate(data, tc)
    if case is None:
        return tc, used, local_unresolved, False
    return case, used, local_unresolved, True


def _sub_all(
    cases: list[TestCase], table: dict, report: dict
) -> tuple[list[TestCase], set[str], dict[str, set[str]]]:
    unresolved: set[str] = set()
    per_case_keys: dict[str, set[str]] = {}
    out: list[TestCase] = []
    for tc in cases:
        case, used, local_unresolved, rebuilt = _substitute_case(tc, table)
        unresolved |= local_unresolved
        out.append(case)
        report["substitutions"] += len(used)
        if rebuilt and used:
            report["cases_touched"].append(case.tc_id)
            per_case_keys[case.tc_id] = used
    return out, unresolved, per_case_keys


# Replace ``{{EN:KEY}}`` / ``{{AR:KEY}}`` with the documented strings.
#
# 100% string fidelity: the values come from the parsed ticket, never from the
# model. Over-long substituted fields are CLAMPED (not rejected), so a case is
# only kept in its original token-bearing form when re-validation fails for
# some other reason -- and ``sweep_residual_placeholders`` then cleans that up.
# Returns ``(cases, report)``. Never raises.
def substitute_placeholders(
    cases: list[TestCase], pairs: list[LanguagePair]
) -> tuple[list[TestCase], dict]:
    """Replace placeholders with the documented strings; never raises."""
    report = new_report(len(pairs))
    if not cases:
        return list(cases or []), report
    try:
        out, unresolved, per = _sub_all(cases, _lookup(pairs), report)
        _fill_coverage_report(report, CoverageInputs(per, unresolved, out, pairs))
        return out, report
    except Exception:
        logger.exception("substitute_placeholders failed - cases returned unchanged")
        return list(cases), report
