"""Deterministic detector for screen/dialog IDENTIFIER COLLISIONS in a ticket.

D2 (2026-08-21). On the SHYJ-5646 run the ticket's own tables bound ``DF03`` to
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
(``agents/host_mode.build_dup_shortlist``), read in the opposite direction: F08
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
import re

logger = logging.getLogger(__name__)

_MAX_INPUT_CHARS = 200_000
_MAX_OCCURRENCES = 2_000
_MAX_IDENTIFIERS = 200
_MAX_BINDINGS_PER_ID = 40
_MAX_FINDINGS = 5
_LABEL_MAX_CHARS = 80
_LABEL_MAX_TOKENS = 6
_MAX_OVERLAP = 0.0
_TOKEN_FOLD_CHARS = 5
_MIN_TOKEN_CHARS = 4
_MIN_BINDING_TOKENS = 2

_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_])(DF|UI|AF|BR)(\d{1,3})(?![A-Za-z0-9_])", re.IGNORECASE
)
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
# The emphasis class is load-bearing, and it was found by running this detector
# over the REAL SHYJ-5646 ticket rather than over a fixture: it returned [] on
# the very collision it was written for. That ticket writes its screen table as
# `| **DF03** | Return bottom sheet: ... |`, and requiring the delimiter to
# follow the id IMMEDIATELY meant the `**` suppressed the definitional binding
# -- so guard 1, which demands one, could never fire and EVERY table definition
# in the ticket was invisible. Bold/italic/code around an id is the normal
# rendering of a Jira or markdown spec table, not an edge case. Bounded to 3
# characters so this stays a delimiter tolerance and not a general scan.
_DEFINITIONAL_RE = re.compile(r"^[*_~`]{0,3}[ \t]*[=|][ \t]*(.*)$")
_TERMINATOR_RE = re.compile(r"[.;|]")
_BEFORE_CUT_RE = re.compile(r"[.;|:]")
_LATIN_RE = re.compile(r"^[a-z]", re.IGNORECASE)

_GENERIC = frozenset(
    """
screen screens page pages dialog dialogs sheet sheets modal modals popup popups
window windows view views form forms flow flows section sections bottom
button buttons banner banners list lists item items field fields toast toasts
tabs card cards label labels alert alerts snackbar header footer icon menu
""".split()
)

_FUNCTION = frozenset(
    """
this that these those when while with without into onto over under after before
during from there here their they them your ours
system user users application customer cardholder tester
display displays displayed show shows shown open opens opened close closes closed
navigate navigates navigated taps tapped click clicks clicked press presses
select selects selected sees seen goes went land lands landed applies apply
block blocks blocked cover covers covered contain contains reach reaches
step steps main
rule rules business requirement requirements criterion criteria case cases
note notes refer refers above below also then each other same such only both
will must shall should where which what does done using used uses
""".split()
)


def _safe_label(text: object) -> str:
    try:
        raw = str(text or "")
        raw = "".join(ch if ch.isprintable() else " " for ch in raw)
        raw = raw.replace("`", "")
        raw = " ".join(raw.split())
        return raw.strip(" -=:|*#>").strip()[:_LABEL_MAX_CHARS]
    except Exception:
        return ""


def _drop(token: str) -> bool:
    low = token.lower()
    return (
        len(low) < _MIN_TOKEN_CHARS
        or low in _FUNCTION
        or bool(_ID_RE.fullmatch(low.upper()))
    )


def _fold(tokens) -> frozenset:
    return frozenset(t.lower()[:_TOKEN_FOLD_CHARS] for t in tokens)


def _is_translation(a: frozenset, b: frozenset) -> bool:
    a_latin = [bool(_LATIN_RE.match(t)) for t in a]
    b_latin = [bool(_LATIN_RE.match(t)) for t in b]
    return (all(a_latin) and not any(b_latin)) or (all(b_latin) and not any(a_latin))


def _definition_tokens(tail: str) -> list:
    out = []
    for t in _TOKEN_RE.findall(tail)[:_LABEL_MAX_TOKENS]:
        if t.lower() in _GENERIC or _drop(t):
            continue
        out.append(t)
    return out


def _usage_tokens(before: str) -> list:
    out: list = []
    for t in reversed(_TOKEN_RE.findall(before)):
        if len(out) >= _LABEL_MAX_TOKENS:
            break
        if t.lower() in _GENERIC:
            continue
        if _drop(t):
            break
        out.append(t)
    out.reverse()
    return out


def _bindings_in_line(line: str, start: int, end: int) -> list:
    out = []
    m = _DEFINITIONAL_RE.match(line[end:])
    if m:
        tail = m.group(1)
        cut = _TERMINATOR_RE.search(tail)
        if cut:
            tail = tail[: cut.start()]
        toks = _definition_tokens(tail)
        if len(toks) >= _MIN_BINDING_TOKENS:
            out.append(("definition", _safe_label(tail), _fold(toks)))
    before = line[:start]
    cut2 = None
    for m2 in _BEFORE_CUT_RE.finditer(before):
        cut2 = m2.end()
    if cut2 is not None:
        before = before[cut2:]
    toks_u = _usage_tokens(before)
    if len(toks_u) >= _MIN_BINDING_TOKENS:
        out.append(("usage", _safe_label(" ".join(toks_u)), _fold(toks_u)))
    return out


def find_identifier_collisions(text: object) -> list:
    findings: list = []
    try:
        raw = str(text or "")[:_MAX_INPUT_CHARS]
        if not raw:
            return []
        by_id: dict = {}
        order: list = []
        seen = 0
        for line in raw.splitlines():
            if seen > _MAX_OCCURRENCES:
                break
            for m in _ID_RE.finditer(line):
                seen += 1
                if seen > _MAX_OCCURRENCES:
                    break
                ident = f"{m.group(1).upper()}{int(m.group(2)):02d}"
                if ident not in by_id:
                    if len(by_id) >= _MAX_IDENTIFIERS:
                        continue
                    by_id[ident] = []
                    order.append(ident)
                bucket = by_id[ident]
                if len(bucket) >= _MAX_BINDINGS_PER_ID:
                    continue
                bucket.extend(_bindings_in_line(line, m.start(), m.end()))
        for ident in order:
            hit = None
            bucket = by_id.get(ident) or []
            for d in bucket:
                if d[0] != "definition":
                    continue
                for other in bucket:
                    if other is d:
                        continue
                    if _is_translation(d[2], other[2]):
                        continue
                    if len(d[2] & other[2]) / len(d[2] | other[2]) > _MAX_OVERLAP:
                        continue
                    hit = (d, other)
                    break
                if hit:
                    break
            if hit is None:
                continue
            labels = []
            for b in hit:
                if b[1] and b[1] not in labels:
                    labels.append(b[1])
            findings.append({"identifier": ident, "bindings": labels})
            if len(findings) >= _MAX_FINDINGS:
                break
    except Exception:
        logger.exception("find_identifier_collisions failed -- returning findings")
    return findings
