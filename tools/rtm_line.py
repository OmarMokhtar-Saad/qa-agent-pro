"""The single-line RTM coverage stat used in compact summaries."""

from __future__ import annotations

import logging

from tools.models import TestCase
from tools.rtm_sanitize import AcceptanceCriterion
from tools.rtm_tracemap import _trace_map

logger = logging.getLogger("tools.rtm")


def _headline(covered: int, total: int, derived: bool) -> str:
    """The `Requirements: n/m ... traced` headline, with the orphan clause."""
    orphans = total - covered
    kind = "MODEL-DERIVED acceptance criteria" if derived else "acceptance criteria"
    line = f"\n\n**Requirements:** {covered}/{total} {kind} traced"
    line += f", {orphans} orphan(s)." if orphans else ", all covered."
    return line


# 2026-08-31 (F4): rtm_oneline used to re-derive its own id match from
# normalize_ac_id, which is the ONE thing _trace_map's docstring says it
# exists to prevent. The two then disagreed inside a single reply: the
# orphan line said 19 of 66 cases were untraced and this line, four
# lines above it, said 66 of 66 were -- because only _trace_map had
# learned to match a case tagged `BR07` to the criterion that IS BR07.
#
# Returns empty string when acs is empty. Never raises.
#
# ``derived=True`` means the criteria were NOT read from the ticket -- the host's
# chat model synthesized them as scaffolding. 2026-08-03: a real run finalized
# with this line reading "6/6 acceptance criteria traced, all covered" against
# six criteria the model had invented, because the ticket carried none. The
# honest disclosure did exist, but in a separate block ABOVE; this line sits in
# the headline stats next to Risk, and on its own it reads as verified
# traceability. 100% coverage of invented requirements is not evidence of
# anything, so the provenance has to travel WITH the number rather than near it.
# Defaults False, so a ticket that really carried criteria is unchanged.
#
# F04 (2026-08-16) applied the same reasoning to a second missing number. The
# line reports TWO figures now: how many criteria have at least one case, and
# how many CASES trace to a criterion. A case whose ``requirement_id`` is null
# -- or names an id this AC list does not contain -- counts as untraced, which
# is why the second figure can fall short of the suite size while the first
# still reads "all covered".
def rtm_oneline(
    acs: list[AcceptanceCriterion],
    test_cases: list[TestCase],
    derived: bool = False,
) -> str:
    """Return a single-line RTM coverage stat (no table) for compact summaries."""
    try:
        if not acs:
            return ""
        ac_to_tcs, orphan_tc_ids = _trace_map(list(acs), list(test_cases or []))
        covered = sum(1 for tcs in ac_to_tcs.values() if tcs)
        line = _headline(covered, len(acs), derived)
        line += _oneline_tail(list(test_cases or []), orphan_tc_ids, derived)
        return line
    except Exception:  # pragma: no cover - defensive, never break the summary
        logger.exception("rtm_oneline failed — returning empty string")
        return ""


# F04 (2026-08-16): the AC figure ALONE read "7/7 acceptance criteria
# traced, all covered" over a suite whose deterministic checklist matcher
# mapped 21 of its 96 cases. The two figures answer different questions —
# how many criteria got at least one case, versus how many cases verify a
# stated criterion — and printing only the first is what let a suite of
# convenience tags read as fully traced. They travel together from here,
# so "all covered" can never stand alone. Counts only (the clause grows
# with integer WIDTH, never with the suite), and silent when there are no
# cases at all, which keeps the stored-suite re-render byte-identical.
def _oneline_tail(cases: list, orphan_tc_ids: list, derived: bool) -> str:
    """The case-trace clause and the derived-criteria disclosure of rtm_oneline."""
    tail = ""
    if cases:
        untraced = len(orphan_tc_ids)
        traced = len(cases) - untraced
        tail += f" {traced} of {len(cases)} case(s) trace to one of them"
        tail += f"; {untraced} trace to none." if untraced else "."
    if derived:
        tail += (
            " They were synthesized because the ticket carried none, so this "
            "measures self-consistency, NOT coverage of stated requirements."
        )
    return tail
