"""Part of the quality-check split (see tools/quality_checks.py): module names."""

from __future__ import annotations

import logging

from tools.models import TestCase
from tools.quality_module_merges import _qualifier_prefix_merges

logger = logging.getLogger("tools.quality_checks")


def _bucket_by_key(cases: list[TestCase]) -> dict[str, list[TestCase]]:
    """Group cases by the casefolded, whitespace-collapsed module key."""
    buckets: dict[str, list[TestCase]] = {}
    for tc in cases:
        key = " ".join((tc.module or "").split()).casefold()
        buckets.setdefault(key, []).append(tc)
    return buckets


def _needs_case_pass(buckets: dict[str, list[TestCase]]) -> bool:
    """True when any key group carries more than one exact spelling."""
    return not all(
        len({tc.module for tc in members}) <= 1 for members in buckets.values()
    )


# 2026-08-01: a real host-mode run (8 parallel category workers, each blind to
# the others' output) produced ONE feature split across two module labels --
# "Cancel order" (60 cases) and "Cancel Order" (36 cases) -- since
# `TestCase.module` is unconstrained free text and nothing merges the workers'
# independent choices, fragmenting every "group by module" view (Jira, TestRail,
# the XLSX pivot). Groups modules by a case/whitespace-insensitive key and
# rewrites every case's `module` to the single exact spelling used by the MOST
# cases in its group. Never merges across genuinely different modules (the key
# is casefolded + whitespace-collapsed, not fuzzy); a no-op whenever every group
# already has one spelling. Never raises: any error returns cases unchanged.
#
# The early return must NOT skip the qualifier pass: a suite can carry a
# qualifier-prefixed variant with NO casing variant at all, and returning there
# would leave it split (the 2026-08-03 bug `merge_qualifier_prefixes` fixes).
def normalize_module_names(
    cases: list[TestCase], merge_qualifier_prefixes: bool = False
) -> list[TestCase]:
    """Canonicalize `module` casing/whitespace across a merged suite."""
    try:
        if not cases:
            return cases
        buckets = _bucket_by_key(cases)
        needs_case_pass = _needs_case_pass(buckets)
        if not needs_case_pass and not merge_qualifier_prefixes:
            return cases
        canonical = _majority_spellings(buckets) if needs_case_pass else {}
        out = _apply_canonical_modules(cases, canonical, buckets)
        if merge_qualifier_prefixes:
            out = _merge_qualifier_labels(out)
        return out
    except Exception:
        logger.exception("normalize_module_names failed — returning cases unchanged")
        return cases


# Majority casing per key group: the single spelling used by the most cases in
# that group wins over any minority variant sharing the key.
def _majority_spellings(buckets: dict[str, list[TestCase]]) -> dict[str, str]:
    """Key -> the module spelling used by the most cases in that key group."""
    canonical: dict[str, str] = {}
    for key, members in buckets.items():
        counts: dict[str, int] = {}
        for tc in members:
            counts[tc.module] = counts.get(tc.module, 0) + 1
        canonical[key] = max(counts.items(), key=lambda kv: kv[1])[0]
    return canonical


def _log_canonicalized(changed: int, buckets: dict[str, list[TestCase]]) -> None:
    logger.info(
        "normalize_module_names: canonicalized %d case(s) across %d "
        "module name variant(s)",
        changed,
        sum(
            1 for members in buckets.values() if len({tc.module for tc in members}) > 1
        ),
    )


def _apply_canonical_modules(
    cases: list[TestCase],
    canonical: dict[str, str],
    buckets: dict[str, list[TestCase]],
) -> list[TestCase]:
    """Rewrite each case's module to its canonical spelling and log the count."""
    changed = 0
    out: list[TestCase] = []
    for tc in cases:
        key = " ".join((tc.module or "").split()).casefold()
        target = canonical.get(key, tc.module)
        if target != tc.module:
            changed += 1
            out.append(tc.model_copy(update={"module": target}))
        else:
            out.append(tc)
    if changed:
        _log_canonicalized(changed, buckets)
    return out


def _label_counts(cases: list[TestCase]) -> dict[str, int]:
    """Exact module label -> number of cases carrying it."""
    label_counts: dict[str, int] = {}
    for tc in cases:
        label_counts[tc.module] = label_counts.get(tc.module, 0) + 1
    return label_counts


def _merge_qualifier_labels(out: list[TestCase]) -> list[TestCase]:
    """Fold qualifier-prefixed module labels onto the bare label they qualify."""
    merges = _qualifier_prefix_merges(_label_counts(out))
    if merges:
        out = [
            (
                tc.model_copy(update={"module": merges[tc.module]})
                if tc.module in merges
                else tc
            )
            for tc in out
        ]
        logger.info(
            "normalize_module_names: merged %d qualifier-prefixed label(s): %s",
            len(merges),
            "; ".join(f"{k!r} -> {v!r}" for k, v in sorted(merges.items())),
        )
    return out
