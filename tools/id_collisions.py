"""Deterministic detector for screen/dialog IDENTIFIER COLLISIONS in a ticket.

D2 (2026-08-21). On the TICKET-5646 run the ticket's own tables bound ``DF03`` to
the return-reason bottom sheet and ``DF04`` to the success screen, while the
Basic Flow prose said "System displays Success message **DF03**". The generator
inherited the collision and SPLIT on it: 22 cases called the success screen DF03
and 8 called it DF04, so the suite contradicts itself and the chat summary
contradicts 22 of its own cases. ``host_ambiguity_severity`` came back ``low``
and nothing was raised to the tester.

That is a class of ambiguity a model is not needed for: the same identifier bound
to two different things in ONE source is decidable by reading. This module
decides it, and nothing more. It is DETECT-AND-REPORT ONLY -- it never resolves a
collision, never blocks generation, and never touches ``host_ambiguity_severity``
(the host self-reports that value; the server explicitly does not classify).

PRECISION OVER RECALL, deliberately, because a false collision reported to every
tester on every run is worse than a missed one. The FIRST two implementations of
this module were rejected on MEASURED false positives, not on review opinion, and
each surviving guard below is the fix for a specific counterexample that is now
pinned in tests/test_id_collisions.py:

1. **A DEFINITIONAL binding is required**, and only ``| ID | label |`` (a table
   cell) or ``ID = label`` (an assignment) counts. A ``ID:`` prose tail does NOT:
   "DF05: shipping label is generated" / "DF05: the courier receives a pickup
   request" are two ELABORATIONS of one screen, not two bindings, and reading
   them as competing definitions produced false collisions on correct tickets.
   Two loose prose mentions likewise never produce a finding.
2. **Both sides must contribute at least ``_MIN_BINDING_TOKENS`` (2) folded
   content tokens.** This is the single most important guard. A one-token
   definition such as ``| DF04 | Success screen |`` -> ``{"succe"}`` is not
   specific enough to accuse anything: with total disjointness as the threshold,
   ANY nearby phrase not containing that one word becomes a "collision"
   ("...the Return Requested message DF04", "...taps Continue DF03",
   "...the Order Cancelled banner DF04" -- all correct, all reported).
3. **The usage side is a contiguous noun phrase**, read backwards from the id and
   stopped at the first function word, short token or other identifier -- not a
   bag of nearby words. A bag reported "Steps 1-3 use DF01", "See DF03 and DF04",
   "Rule BR09 blocks ..." and "Note: BR09 applies to AF02".
4. **A translation is not a collision.** If one side's tokens are wholly Latin and
   the other's wholly non-Latin, it is the same label in two scripts. Without
   this, a bilingual ticket (``| DF06 | Order details screen |`` /
   ``| DF06 | ...Arabic... |``) lights up on EVERY id, because the two scripts are
   trivially disjoint.
5. **The token sets must be COMPLETELY disjoint** (``_MAX_OVERLAP = 0.0``), after
   folding each token to its first ``_TOKEN_FOLD_CHARS`` characters. A partial
   overlap is almost always one thing worded twice; only a wholly disjoint
   vocabulary is evidence of two things. The prefix fold is what keeps
   "Confirmation page DF02" from colliding with "| DF02 | Review and Confirm
   dialog |" -- confirm/confirmation both fold to ``confi``.

``_GENERIC`` strips type nouns (screen, sheet, dialog, button, banner, list,
field, ...) because they carry no discriminating information. ``message`` is
deliberately NOT in that set: adding it was measured to DELETE the second token
of "Success message" and thereby lose BOTH true positives this detector exists
for, while removing no false positive at all -- guard 2 already covers the class
that widening was aimed at.

MEASURED, by ablation, on the corpus in tests/test_id_collisions.py (21 shapes
that must stay empty, 3 that must stay found). Removing guard 2 alone reintroduces
4 false positives; guard 1 alone, 2; guard 4 alone, 1; the widened ``_GENERIC``
alone, 1. Nothing in the corpus is redundant, and the full clean ticket -- 7
identifiers over 13 mentions, table rows, numbered prose, a cross-reference list,
a back-reference, a rule sentence, a ``Note:`` line and an Arabic line -- yields
ZERO findings, so on a clean ticket this detector adds NOTHING to the payload and
leaves ``instructions`` byte-identical.

The similarity measure is the F08 title-token Jaccard idiom
(``agents/host_dedup.build_dup_shortlist``), read in the opposite direction: F08
flags a HIGH overlap between two cases, this flags a ZERO overlap between two
bindings. The tokeniser is re-declared here rather than imported because
``tools/`` must not import ``agents/`` -- that edge is what lets an agent be
exercised without the MCP transport, and importing it backwards would invert it.
The two regexes are also not the same: F08 keeps hyphenated terms and formatted
numbers whole because it compares TITLES; this one wants plain word tokens.

Pure, synchronous, stdlib only -- no LLM (there is no backend), no embeddings, no
I/O, no settings flag. UNTRUSTED input: every bound is hard and every one is
proved to BIND by a test (input length, occurrence count, identifier count,
bindings per id, findings, label length), and every label is stripped of
backticks, newlines and non-printables and capped, so nothing quoted back into
the payload can break out of the JSON value it sits in. Never raises -- a
detector must never be able to break a generation, exactly like
tools/coverage_classes.py beside it.
"""

from __future__ import annotations

import logging

from tools.idcl_scan import collect_bindings, conflict_labels, first_conflict

logger = logging.getLogger(__name__)

_MAX_INPUT_CHARS = 200_000
_MAX_FINDINGS = 5


def find_identifier_collisions(text: object) -> list:
    findings: list = []
    try:
        raw = str(text or "")[:_MAX_INPUT_CHARS]
        if not raw:
            return []
        by_id, order = collect_bindings(raw)
        for ident in order:
            hit = first_conflict(by_id.get(ident) or [])
            if hit is None:
                continue
            findings.append({"identifier": ident, "bindings": conflict_labels(hit)})
            if len(findings) >= _MAX_FINDINGS:
                break
    except Exception:
        logger.exception("find_identifier_collisions failed -- returning findings")
    return findings
