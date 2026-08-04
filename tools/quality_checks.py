"""Cheap heuristic quality gate for generated test cases.

Flags vague step actions (e.g. "enter any value") and placeholder test_data
(e.g. "anything") so drift can't silently reach the exported files. This is a
bounded, regex-based heuristic — not a semantic check — kept intentionally
cheap so it can run on every category's output without an extra LLM call.
Never raises to callers.

Deliberately does NOT touch technical/security step *content* — it only flags
vague phrasing versus a literal value; a step that opens DevTools, inspects
headers, or uses a SQL/XSS payload is fine as long as the payload/value/field
is spelled out.
"""

from __future__ import annotations

import logging
import re

from tools.models import TestCase

logger = logging.getLogger(__name__)

# Vague/non-concrete step action phrasing — the step names an action but not
# the literal value/payload used, e.g. "enter a classic SQL injection string"
# instead of "Enter ' OR '1'='1 into the 'Username' field".
_VAGUE_ACTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"\benter\s+(a|an|any|some)\s+valid\b", re.IGNORECASE),
    re.compile(r"\benter\s+any\s+value\b", re.IGNORECASE),
    re.compile(r"\benter\s+some\s+value\b", re.IGNORECASE),
    re.compile(r"\benter\s+a\s+random\b", re.IGNORECASE),
    re.compile(r"\buse\s+a\s+random\b", re.IGNORECASE),
    re.compile(r"\b(a|an)\s+classic\s+sql\s+injection\s+string\b", re.IGNORECASE),
    re.compile(r"\ba\s+sql\s+injection\s+string\b(?!\s*[:(].{0,60})", re.IGNORECASE),
    re.compile(r"\bany\s+(invalid|incorrect)\s+value\b", re.IGNORECASE),
    re.compile(r"\bsome\s+(invalid|incorrect)\s+data\b", re.IGNORECASE),
]

# Vague expected_result phrasing — asserts a qualifier ("appropriate error
# message", "works correctly") instead of the concrete observable outcome the
# tester should verify (the exact on-screen message, field/button state, or
# resulting page). These qualifiers are almost never followed by the actual
# text, so flagging them has a low false-positive rate.
_VAGUE_EXPECTED_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"\b(appropriate|proper|suitable|relevant)\s+(error\s+)?(message|response|validation|feedback)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(behaves?|works?|functions?)\s+(correctly|as\s+expected)\b", re.IGNORECASE
    ),
    re.compile(
        r"\bhandled\s+(gracefully|properly|appropriately|correctly)\b", re.IGNORECASE
    ),
    re.compile(r"\bvalidat(?:es|ed|ion)\s+properly\b", re.IGNORECASE),
]

# Placeholder test_data values — a field name with no concrete example value.
_PLACEHOLDER_DATA_VALUES = {
    "anything",
    "any value",
    "any password",
    "any data",
    "some value",
    "valid data",
    "n/a",
    "na",
    "tbd",
    "todo",
}


# A concrete value present in the same text — a quoted literal, a 4+ digit run
# (phone/id/amount), or an email — means the step/result is actionable even if it
# also contains a "valid X" / "appropriate message" qualifier, so it should NOT be
# flagged as vague (e.g. "Enter a valid mobile number '01111222333'").
_CONCRETE_VALUE_RE = re.compile(r"""'[^']{2,}'|"[^"]{2,}"|\d{4,}|@\w+\.\w+""")


def _has_concrete_value(text: str) -> bool:
    try:
        return bool(_CONCRETE_VALUE_RE.search(text))
    except Exception:
        return False


def _is_vague_action(action: str) -> bool:
    try:
        if not any(p.search(action) for p in _VAGUE_ACTION_PATTERNS):
            return False
        return not _has_concrete_value(action)
    except Exception:
        return False


def _is_vague_expected(expected: str | None) -> bool:
    if not expected:
        return False
    try:
        if not any(p.search(expected) for p in _VAGUE_EXPECTED_PATTERNS):
            return False
        return not _has_concrete_value(expected)
    except Exception:
        return False


def _is_placeholder_data(test_data: str | None) -> bool:
    if not test_data:
        return False
    try:
        normalized = test_data.strip().lower()
        if normalized in _PLACEHOLDER_DATA_VALUES:
            return True
        # "field: <placeholder>" form, e.g. "password: anything"
        for part in re.split(r"[,;\n]", normalized):
            value = part.split(":", 1)[-1].strip()
            if value in _PLACEHOLDER_DATA_VALUES:
                return True
        return False
    except Exception:
        return False


def find_vague_steps(cases: list[TestCase]) -> list[tuple[str, int, str]]:
    """Return (tc_id, step_number, action) for every step with vague phrasing. Never raises."""
    try:
        return [
            (tc.tc_id, step.step_number, step.action)
            for tc in cases
            for step in tc.steps
            if _is_vague_action(step.action)
        ]
    except Exception:
        logger.exception("find_vague_steps failed — returning empty list")
        return []


def find_vague_expected(cases: list[TestCase]) -> list[tuple[str, int, str]]:
    """Return (tc_id, step_number, expected_result) for every step whose expected
    result uses vague qualifier phrasing instead of a concrete outcome. Never raises."""
    try:
        return [
            (tc.tc_id, step.step_number, step.expected_result)
            for tc in cases
            for step in tc.steps
            if _is_vague_expected(step.expected_result)
        ]
    except Exception:
        logger.exception("find_vague_expected failed — returning empty list")
        return []


def find_placeholder_data(cases: list[TestCase]) -> list[tuple[str, int, str]]:
    """Return (tc_id, step_number, test_data) for every step with placeholder test data. Never raises."""
    try:
        return [
            (tc.tc_id, step.step_number, step.test_data)
            for tc in cases
            for step in tc.steps
            if _is_placeholder_data(step.test_data)
        ]
    except Exception:
        logger.exception("find_placeholder_data failed — returning empty list")
        return []


def quality_ratio(cases: list[TestCase]) -> float:
    """Fraction of steps that are vague and/or have placeholder test data. Never raises."""
    try:
        total_steps = sum(len(tc.steps) for tc in cases)
        if total_steps == 0:
            return 0.0
        flagged_steps: set[tuple[str, int]] = set()
        for tc_id, step_number, _ in find_vague_steps(cases):
            flagged_steps.add((tc_id, step_number))
        for tc_id, step_number, _ in find_placeholder_data(cases):
            flagged_steps.add((tc_id, step_number))
        for tc_id, step_number, _ in find_vague_expected(cases):
            flagged_steps.add((tc_id, step_number))
        return len(flagged_steps) / total_steps
    except Exception:
        logger.exception("quality_ratio failed — returning 0.0")
        return 0.0


def quality_warning_section(cases: list[TestCase], max_examples: int = 10) -> str:
    """Build a '## Data Quality Notes' markdown block flagging vague/placeholder
    content that survived generation. Returns '' when nothing is flagged or on
    any internal error. Never raises."""
    try:
        vague = find_vague_steps(cases)
        placeholder = find_placeholder_data(cases)
        vague_expected = find_vague_expected(cases)
        if not vague and not placeholder and not vague_expected:
            return ""

        lines = ["\n\n## Data Quality Notes\n"]
        if vague:
            lines.append(
                f"- {len(vague)} step(s) may use vague phrasing instead of a literal "
                "value — please review before executing:"
            )
            for tc_id, step_number, action in vague[:max_examples]:
                lines.append(f"  - {tc_id} step {step_number}: {action[:120]}")
        if placeholder:
            lines.append(
                f"- {len(placeholder)} step(s) have placeholder test data instead of "
                "a concrete example value:"
            )
            for tc_id, step_number, test_data in placeholder[:max_examples]:
                lines.append(f"  - {tc_id} step {step_number}: {test_data}")
        if vague_expected:
            lines.append(
                f"- {len(vague_expected)} step(s) have a vague expected result "
                '(e.g. "appropriate error message") instead of the concrete outcome '
                "to verify:"
            )
            for tc_id, step_number, expected in vague_expected[:max_examples]:
                lines.append(f"  - {tc_id} step {step_number}: {expected[:120]}")
        return "\n".join(lines)
    except Exception:
        logger.exception("quality_warning_section failed — returning empty string")
        return ""


def resolve_chained_refs_to_stable(cases: list[TestCase]) -> list[TestCase]:
    """Rewrite category-local ``chained_from`` tc_ids to the target case's stable_id.

    Called per CategoryResult BEFORE cross-category flatten, while a tc_id still
    uniquely identifies a case WITHIN its own category (every category numbers from
    TC-001, so a raw tc_id becomes ambiguous once categories are merged). The LLM
    only ever sees its own category's ids, so a chained ref can only mean a case in
    the same batch. We resolve it to that case's content stable_id — which survives
    the flatten, dedup and the final renumber — and clear any ref that names no case
    in this category. Returns a new list; never mutates; never raises.
    """
    try:
        local = {tc.tc_id: tc.stable_id for tc in cases}
        out: list[TestCase] = []
        for tc in cases:
            if not tc.test_data:
                out.append(tc)
                continue
            new_items = []
            changed = False
            for it in tc.test_data:
                if it.strategy == "chained" and it.chained_from:
                    target = local.get(it.chained_from)
                    if target is None:
                        logger.info(
                            "resolve_chained_refs_to_stable: clearing chained_from "
                            "%r on %s field %r — no such case in category",
                            it.chained_from,
                            tc.tc_id,
                            it.field,
                        )
                        # genpipe L3: a dangling chained ref is no longer a chain —
                        # downgrade the strategy so no consumer treats it as one.
                        it = it.model_copy(
                            update={"chained_from": None, "strategy": "static"}
                        )
                        changed = True
                    elif target != it.chained_from:
                        it = it.model_copy(update={"chained_from": target})
                        changed = True
                new_items.append(it)
            out.append(
                tc.model_copy(update={"test_data": new_items}) if changed else tc
            )
        return out
    except Exception:
        logger.exception(
            "resolve_chained_refs_to_stable failed — returning cases unchanged"
        )
        return cases


def restore_chained_refs_from_stable(cases: list[TestCase]) -> list[TestCase]:
    """Rewrite stable_id ``chained_from`` values back to the FINAL renumbered tc_id.

    Counterpart to resolve_chained_refs_to_stable, run AFTER the final TC-001..N
    renumber. Each chained_from now holds the target case's stable_id (set at the
    per-category boundary); map it to that case's final tc_id. A stable_id no longer
    present (target deduped/dropped) is a dangling ref — cleared to None and logged
    so testers never see a wrong prerequisite pointer. New list; never raises.
    """
    try:
        by_stable = {tc.stable_id: tc.tc_id for tc in cases}
        out: list[TestCase] = []
        for tc in cases:
            if not tc.test_data:
                out.append(tc)
                continue
            new_items = []
            changed = False
            for it in tc.test_data:
                if it.strategy == "chained" and it.chained_from:
                    final_id = by_stable.get(it.chained_from)
                    if final_id is None:
                        logger.info(
                            "restore_chained_refs_from_stable: clearing dangling "
                            "chained_from on %s field %r (target case dropped)",
                            tc.tc_id,
                            it.field,
                        )
                        # genpipe L3: downgrade a dangling chain to a plain static
                        # value so no exporter renders a broken prerequisite.
                        it = it.model_copy(
                            update={"chained_from": None, "strategy": "static"}
                        )
                        changed = True
                    elif final_id != it.chained_from:
                        it = it.model_copy(update={"chained_from": final_id})
                        changed = True
                new_items.append(it)
            out.append(
                tc.model_copy(update={"test_data": new_items}) if changed else tc
            )
        return out
    except Exception:
        logger.exception(
            "restore_chained_refs_from_stable failed — returning cases unchanged"
        )
        return cases


_MODULE_SEPARATORS = ("-", "\u2013", "\u2014", ":", ">", "/", "|")


def _qualifier_prefix_merges(counts: dict[str, int]) -> dict[str, str]:
    """Map a QUALIFIER-PREFIXED module label onto the bare label it qualifies.

    ``counts`` is {exact module label: number of cases}. Returns
    {qualified_label: bare_label} for the safe subset only; every other pair is
    left alone.

    2026-08-03. normalize_module_names' first pass merges only CASING/whitespace
    variants, so a real suite shipped one feature under two labels:
    "Cancel Order" (86 cases) and "Sehhaty Store - Cancel Order" (12) --
    different bucket keys, never merged, and every "group by module" view (Jira,
    TestRail, the XLSX pivot) fragmented for what is one feature.

    Merges ONLY on TAIL containment -- the bare label must be the TRAILING segment
    of the qualified one, after a separator:

        "Cancel Order"  <- "Sehhaty Store - Cancel Order"   MERGE

    and NEVER on HEAD containment, which is how a genuine SUB-module is named:

        "Store Wallet"  <- "Store Wallet - Top Up"          REFUSE
        "Checkout"      <- "Checkout - Guest"               REFUSE
        "Profile"       <- "Profile: Addresses"             REFUSE

    That asymmetry IS the rule, and it is why plain containment (or a token-count
    threshold) cannot work: in "<qualifier> - <thing>" naming the trailing segment
    is the thing and the leading segment is its scope (product, app, area). Two
    labels sharing a TAIL are one thing at two qualification depths; two labels
    sharing a HEAD are different things inside one area.

    Second guard -- refuse when TWO OR MORE distinct qualified labels share one
    tail. "Admin - Login" and "User - Login" both tail-match "Login", and
    collapsing them would destroy precisely the distinction those labels encode.
    Only a 1:1 qualified->bare mapping merges.

    Script-agnostic on purpose: matching is on separator-delimited segments, never
    on casing, so it behaves identically for the Arabic labels that make up a
    large share of real suites (``str.casefold()`` is a no-op for Arabic, which
    the first pass silently depends on).
    """

    def _norm(label: object) -> str:
        return " ".join(str(label or "").split())

    # Keyed CASEFOLDED, and the winner within a key is the spelling used by the
    # MOST cases. 2026-08-03: this lookup used to be exact-text, which silently
    # defeated the whole rule the first time a real suite disagreed on case. The
    # observed split was "Cancel Order" (49) + "Sehhaty Cancel Order" (10) +
    # "Sehhaty Store - Cancel order" (20): the qualified label's tail is
    # "Cancel order" but the bare label present is "Cancel Order", so the exact
    # match failed and _qualifier_prefix_merges returned {} for exactly the
    # three-way split it exists to close. Majority spelling matches what the
    # casing pass already does, so the two passes cannot disagree on the winner.
    bare_by_key: dict[str, str] = {}
    for label in counts:
        key = _norm(label).casefold()
        cur = bare_by_key.get(key)
        if cur is None or counts.get(label, 0) > counts.get(cur, 0):
            bare_by_key[key] = label

    # Qualifier tokens seen in SEPARATOR-qualified labels, e.g. "Sehhaty Store - X"
    # contributes {sehhaty, store}. Used only to decide whether a SEPARATOR-LESS
    # label is a product-qualified variant; see the guard below.
    known_qualifiers: set = set()
    # tail_key -> {qualified label: the token set that was REMOVED to reach the tail}.
    # The removed tokens are what tell one product family from rival qualifiers.
    tails: dict[str, dict[str, frozenset]] = {}
    for label in counts:
        norm = _norm(label)
        for sep in _MODULE_SEPARATORS:
            token = f" {sep} "
            idx = norm.find(token)
            while idx != -1:
                head = norm[:idx].strip()
                tail = norm[idx + len(token) :].strip()
                if tail and tail != norm:
                    toks = frozenset(t.casefold() for t in head.split() if t)
                    tails.setdefault(tail.casefold(), {})[label] = toks
                    known_qualifiers.update(toks)
                idx = norm.find(token, idx + 1)

    # A SEPARATOR-LESS label can still be a product-qualified variant:
    # "Sehhaty Cancel Order" is "Cancel Order" with a product name glued on. But
    # plain suffix containment is exactly the dangerous rule -- "Order" is a suffix
    # of "Cancel Order" and merging those would be wrong. The discriminator is
    # WHAT was removed: allow it only when every removed prefix token is already a
    # known qualifier token from a separator-qualified label in this same suite.
    # "Sehhaty" qualifies via "Sehhaty Store - ...", so it is allowed; "Cancel"
    # never appears as a qualifier, so "Order" <- "Cancel Order" stays refused.
    for label in counts:
        norm = _norm(label)
        toks = norm.split()
        for cut in range(1, len(toks)):
            prefix = toks[:cut]
            tail = " ".join(toks[cut:])
            if not tail:
                continue
            if not all(t.casefold() in known_qualifiers for t in prefix):
                continue
            tails.setdefault(tail.casefold(), {})[label] = frozenset(
                t.casefold() for t in prefix
            )

    merges: dict[str, str] = {}
    for tail_key, qualified in tails.items():
        target = bare_by_key.get(tail_key)
        if target is None:
            continue
        others = {q: toks for q, toks in qualified.items() if q != target}
        if not others:
            continue
        # ONE qualifier family, or rivals? 2026-08-03: the previous rule refused any
        # tail claimed by more than one qualified label, which correctly rejects
        # "Admin - Login" + "User - Login" but ALSO rejected the real observed
        # split -- "Sehhaty Cancel Order" + "Sehhaty Store - Cancel order" both
        # point at "Cancel Order", and those are three spellings of ONE module, not
        # two sub-modules. The discriminator is the REMOVED tokens: a shared token
        # across every claimant means one product prefix spelled at different
        # depths (sehhaty / sehhaty+store), while disjoint tokens (admin vs user)
        # encode a distinction that merging would destroy.
        if len(others) > 1:
            common = frozenset.intersection(*others.values()) if others else frozenset()
            if not common:
                continue
        for q in others:
            merges[q] = target
    return merges


def normalize_module_names(
    cases: list[TestCase], merge_qualifier_prefixes: bool = False
) -> list[TestCase]:
    """Canonicalize `module` casing/whitespace across a merged suite.

    2026-08-01: a real host-mode run (8 parallel category workers, each blind
    to the others' output) produced ONE feature split across two module
    labels -- "Cancel order" (60 cases) and "Cancel Order" (36 cases) -- since
    `TestCase.module` is unconstrained free text (tools/models.py) and nothing
    merges the workers' independent choices. That fragments every "group by
    module" view (Jira, TestRail, the XLSX pivot) for a suite that is really
    one feature.

    Groups modules by a case/whitespace-insensitive key; within each group,
    rewrites every case's `module` to the single exact spelling used by the
    MOST cases in that group, so the majority casing wins over a minority
    worker's variant. Never merges across genuinely different modules (the
    key is casefolded + whitespace-collapsed, not fuzzy). A no-op whenever
    every group already has just one exact spelling (the common case: nothing
    to fix). Never raises: any error returns cases unchanged.
    """
    try:
        if not cases:
            return cases
        buckets: dict[str, list[TestCase]] = {}
        for tc in cases:
            key = " ".join((tc.module or "").split()).casefold()
            buckets.setdefault(key, []).append(tc)
        needs_case_pass = not all(
            len({tc.module for tc in members}) <= 1 for members in buckets.values()
        )
        # The early return must NOT skip the qualifier pass: a suite can carry a
        # qualifier-prefixed variant with NO casing variant at all, and returning
        # here would leave it split (the 2026-08-03 bug this parameter fixes).
        if not needs_case_pass and not merge_qualifier_prefixes:
            return cases
        # Majority casing per key group: the single spelling used by the most
        # cases in that group wins over any minority variant sharing the key.
        canonical: dict[str, str] = {}
        if needs_case_pass:
            for key, members in buckets.items():
                counts: dict[str, int] = {}
                for tc in members:
                    counts[tc.module] = counts.get(tc.module, 0) + 1
                canonical[key] = max(counts.items(), key=lambda kv: kv[1])[0]
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
            logger.info(
                "normalize_module_names: canonicalized %d case(s) across %d "
                "module name variant(s)",
                changed,
                sum(
                    1
                    for members in buckets.values()
                    if len({tc.module for tc in members}) > 1
                ),
            )
        if merge_qualifier_prefixes:
            label_counts: dict[str, int] = {}
            for tc in out:
                label_counts[tc.module] = label_counts.get(tc.module, 0) + 1
            merges = _qualifier_prefix_merges(label_counts)
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
    except Exception:
        logger.exception("normalize_module_names failed — returning cases unchanged")
        return cases


def data_notes_section(cases: list[TestCase]) -> str:
    """Build a one-line-per-case '## Test Data' markdown note for cases that declare
    a data plan. Returns '' when no case has test_data, so summaries stay
    byte-identical when the feature is off/unused. Never raises.
    """
    try:
        rows: list[str] = []
        for tc in cases:
            if not getattr(tc, "test_data", None):
                continue
            fields = ", ".join(f"{it.field}={it.strategy}" for it in tc.test_data)
            rows.append(f"- {tc.tc_id}: {fields}")
        if not rows:
            return ""
        header = (
            "\n\n## Test Data\n"
            "Each case below declares what data it needs and how to source it "
            "(see the **Test Data** column in the exported file):\n"
        )
        return header + "\n".join(rows)
    except Exception:
        logger.exception("test_data_notes_section failed — returning empty string")
        return ""
