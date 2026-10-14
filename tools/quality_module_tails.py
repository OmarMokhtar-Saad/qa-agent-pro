"""Part of the quality-check split (see tools/quality_checks.py): module tails."""

from __future__ import annotations

from collections.abc import Iterator

_MODULE_SEPARATORS = ("-", "\u2013", "\u2014", ":", ">", "/", "|")


def _norm_label(label: object) -> str:
    """The label with runs of whitespace collapsed to single spaces."""
    return " ".join(str(label or "").split())


def _bare_label_by_key(counts: dict[str, int]) -> dict[str, str]:
    """Casefolded key -> the majority spelling among the labels sharing it."""
    # Keyed CASEFOLDED, and the winner within a key is the spelling used by the
    # MOST cases. 2026-08-03: this lookup used to be exact-text, which silently
    # defeated the whole rule the first time a real suite disagreed on case. The
    # observed split was "Cancel Order" (49) + "Client Cancel Order" (10) +
    # "Client Store - Cancel order" (20): the qualified label's tail is
    # "Cancel order" but the bare label present is "Cancel Order", so the exact
    # match failed and _qualifier_prefix_merges returned {} for exactly the
    # three-way split it exists to close. Majority spelling matches what the
    # casing pass already does, so the two passes cannot disagree on the winner.
    bare_by_key: dict[str, str] = {}
    for label in counts:
        key = _norm_label(label).casefold()
        cur = bare_by_key.get(key)
        if cur is None or counts.get(label, 0) > counts.get(cur, 0):
            bare_by_key[key] = label
    return bare_by_key


def _label_tails(label: str) -> Iterator[tuple[str, frozenset]]:
    """Yield (tail key, removed head tokens) for each separator cut of one label."""
    norm = _norm_label(label)
    for sep in _MODULE_SEPARATORS:
        token = f" {sep} "
        idx = norm.find(token)
        while idx != -1:
            head = norm[:idx].strip()
            tail = norm[idx + len(token) :].strip()
            if tail and tail != norm:
                yield (
                    tail.casefold(),
                    frozenset(t.casefold() for t in head.split() if t),
                )
            idx = norm.find(token, idx + 1)


# known_qualifiers: tokens seen in SEPARATOR-qualified labels ("Client Store - X"
# contributes {client, store}), used only to decide whether a SEPARATOR-LESS
# label is a product-qualified variant. tails: tail_key -> {qualified label: the
# token set REMOVED to reach the tail}; the removed tokens are what tell one
# product family from rival qualifiers.
def _separator_tails(
    counts: dict[str, int],
) -> tuple[dict[str, dict[str, frozenset]], set]:
    """Tail key -> {qualified label: removed head tokens}, plus every qualifier token."""
    known_qualifiers: set = set()
    tails: dict[str, dict[str, frozenset]] = {}
    for label in counts:
        for key, toks in _label_tails(label):
            tails.setdefault(key, {})[label] = toks
            known_qualifiers.update(toks)
    return tails, known_qualifiers


# A SEPARATOR-LESS label can still be a product-qualified variant:
# "Client Cancel Order" is "Cancel Order" with a product name glued on. But
# plain suffix containment is exactly the dangerous rule -- "Order" is a suffix
# of "Cancel Order" and merging those would be wrong. The discriminator is
# WHAT was removed: allow it only when every removed prefix token is already a
# known qualifier token from a separator-qualified label in this same suite.
# "Client" qualifies via "Client Store - ...", so it is allowed; "Cancel"
# never appears as a qualifier, so "Order" <- "Cancel Order" stays refused.
def _add_separatorless_tails(
    counts: dict[str, int],
    tails: dict[str, dict[str, frozenset]],
    known_qualifiers: set,
) -> None:
    """Add the product-qualified separator-less labels to `tails` in place."""
    for label in counts:
        norm = _norm_label(label)
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
