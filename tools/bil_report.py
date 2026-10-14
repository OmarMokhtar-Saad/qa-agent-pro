"""The substitution report: its empty shape and the key-coverage fields."""

from __future__ import annotations

from typing import NamedTuple

from tools import bil_key
from tools.bil_baked import detect_baked_literals
from tools.bil_pair import LanguagePair
from tools.models import TestCase


def new_report(documented_pairs: int = 0) -> dict:
    """An empty substitution report (also the degraded / never-raise result)."""
    return {
        "documented_pairs": documented_pairs,
        "substitutions": 0,
        "unresolved": [],
        "covered_keys": [],
        "missing_keys": [],
        "partial_keys": [],
        "split_keys": [],
        "cases_touched": [],
        "baked_keys": [],
        "residual_tokens": [],
        # stable_ids of the cases that actually carry a documented pair (see
        # rule_packs.protected_stable_ids): a mandated per-key case must never be
        # merged away as a near-duplicate.
        "protected_stable_ids": [],
        "placeholders_seen": False,
    }


def _holders(
    per_case_keys: dict[str, set[str]],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    holder: dict[str, set[str]] = {}
    langs: dict[str, set[str]] = {}
    for tc_id, keys in per_case_keys.items():
        for entry in keys:
            lang, key = entry.split(":", 1)
            holder.setdefault(key, set()).add(tc_id)
            langs.setdefault(key, set()).add(lang)
    return holder, langs


def _cover_keys(
    report: dict, holder: dict[str, set[str]], pairs: list[LanguagePair]
) -> None:
    covered = set(holder)
    report["covered_keys"] = sorted(covered)
    report["missing_keys"] = sorted(
        k
        for k in (bil_key.normalize_key(p.key) for p in pairs)
        if k and k not in covered
    )


def _lang_keys(
    report: dict, holder: dict[str, set[str]], langs: dict[str, set[str]]
) -> None:
    report["partial_keys"] = sorted(k for k, ls in langs.items() if len(ls) < 2)
    report["split_keys"] = sorted(k for k, ids in holder.items() if len(ids) > 1)


# PROTECTION LIST, not a nice-to-have. Two bilingual cases differ only by
# which documented message they quote; a similarity scorer could still
# score that pair above the 0.9 dedup threshold even AFTER substitution,
# were the disabled dedup seam revived, so ordering substitution before
# dedup is necessary but not sufficient. stable_id is derived from
# (title, steps) and the only later mutation before dedup is the sweep's
# model_copy, which does NOT re-run that validator -- so these ids still
# match at the dedup call site.
def _stable_ids(out: list[TestCase], per_case_keys: dict[str, set[str]]) -> list[str]:
    return sorted(
        {
            sid
            for sid in ((tc.stable_id or "") for tc in out if tc.tc_id in per_case_keys)
            if sid
        }
    )


class CoverageInputs(NamedTuple):
    """What was substituted, as ``_fill_coverage_report`` needs it."""

    per_case_keys: dict[str, set[str]]
    unresolved: set[str]
    out: list[TestCase]
    pairs: list[LanguagePair]


def _fill_coverage_report(report: dict, cov: CoverageInputs) -> None:
    """Derive the key-coverage fields of ``report`` from what was substituted."""
    per_case_keys, unresolved, out, pairs = cov
    holder, langs = _holders(per_case_keys)
    _cover_keys(report, holder, pairs)
    _lang_keys(report, holder, langs)
    report["unresolved"] = sorted(unresolved)
    report["protected_stable_ids"] = _stable_ids(out, per_case_keys)
    report["placeholders_seen"] = bool(per_case_keys or unresolved)
    report["baked_keys"] = detect_baked_literals(out, pairs, set(holder))
