"""Part of the quality-check split (see tools/quality_checks.py): module merges."""

from __future__ import annotations

from tools.quality_module_tails import (
    _add_separatorless_tails,
    _bare_label_by_key,
    _separator_tails,
)


# ``counts`` is {exact module label: number of cases}; returns
# {qualified_label: bare_label} for the safe subset only.
#
# 2026-08-03. normalize_module_names' first pass merges only CASING/whitespace
# variants, so a real suite shipped one feature under two labels: "Cancel Order"
# (86 cases) and "Client Store - Cancel Order" (12), and every "group by module"
# view (Jira, TestRail, the XLSX pivot) fragmented.
#
# Merges ONLY on TAIL containment -- the bare label must be the TRAILING segment
# of the qualified one, after a separator:
#     "Cancel Order"  <- "Client Store - Cancel Order"   MERGE
# and NEVER on HEAD containment, which is how a genuine SUB-module is named:
#     "Store Wallet"  <- "Store Wallet - Top Up"          REFUSE
#     "Checkout"      <- "Checkout - Guest"               REFUSE
#     "Profile"       <- "Profile: Addresses"             REFUSE
# That asymmetry IS the rule (plain containment or a token-count threshold cannot
# work): in "<qualifier> - <thing>" naming the trailing segment is the thing and
# the leading one its scope. Labels sharing a TAIL are one thing at two
# qualification depths; labels sharing a HEAD are different things in one area.
#
# Second guard: refuse when TWO OR MORE distinct qualified labels share one
# tail ("Admin - Login" and "User - Login" both tail-match "Login"); only a 1:1
# qualified->bare mapping merges. Script-agnostic on purpose: matching is on
# separator-delimited segments, never casing, so it behaves identically for the
# Arabic labels that are a large share of real suites (``str.casefold()`` is a
# no-op for Arabic, which the first pass silently depends on).
def _qualifier_prefix_merges(counts: dict[str, int]) -> dict[str, str]:
    """Map a QUALIFIER-PREFIXED module label onto the bare label it qualifies."""
    bare_by_key = _bare_label_by_key(counts)
    tails, known_qualifiers = _separator_tails(counts)
    _add_separatorless_tails(counts, tails, known_qualifiers)
    return _merges_from_tails(tails, bare_by_key)


# ONE qualifier family, or rivals? 2026-08-03: the previous rule refused any
# tail claimed by more than one qualified label, which correctly rejects
# "Admin - Login" + "User - Login" but ALSO rejected the real observed split --
# "Client Cancel Order" + "Client Store - Cancel order" both point at "Cancel
# Order", three spellings of ONE module. The discriminator is the REMOVED
# tokens: a shared token across every claimant means one product prefix spelled
# at different depths, while disjoint tokens (admin vs user) encode a
# distinction that merging would destroy.
def _merges_from_tails(
    tails: dict[str, dict[str, frozenset]], bare_by_key: dict[str, str]
) -> dict[str, str]:
    """{qualified label: bare label} for every tail the two guards let through."""
    merges: dict[str, str] = {}
    for tail_key, qualified in tails.items():
        target = bare_by_key.get(tail_key)
        if target is None:
            continue
        others = {q: toks for q, toks in qualified.items() if q != target}
        if not others:
            continue
        if len(others) > 1:
            common = frozenset.intersection(*others.values()) if others else frozenset()
            if not common:
                continue
        for q in others:
            merges[q] = target
    return merges
