"""Byte, row and priority limits of the suite-consistency advisory block."""

from __future__ import annotations

import re

# Cap on reported findings so a pathological suite cannot produce an unbounded
# markdown block.
_MAX_REPORTED = 20

# The whole block's byte budget. NOT a new number: it is the bound
# tests/test_suite_consistency.py has asserted since 2026-08-17, which nothing
# enforced. That bound was measured with two of the six bullets structurally
# absent -- both F05 (ungrounded UI strings) and F11 (bound contradictions) need
# a grounding text over oracle_grounding._MIN_GROUNDING_CHARS, and the test
# passed none -- so the block reached 4173 bytes in a grounded run against a
# 2400 assertion, past mcp_handlers._SUMMARY_CAP (4000) outright.
#
# Why the block and not each bullet: every bullet already caps its own ROW COUNT
# at _MAX_REPORTED, and that is exactly the per-bullet framing that produced this
# -- six individually-bounded bullets whose SUM nobody bounded. F14 is the
# finding that says so.
_BLOCK_CAP = 2400

# Drop priorities for the advisory bullets. LOWER drops FIRST, and each constant
# names WHY that bullet is droppable at all -- a bullet that cannot answer that
# question is protected instead. Mirrors mcp_handlers._REPLY_P_* deliberately,
# including the rule that the rationale is part of the constant.
#
# The axis is: does the bullet say the suite is WRONG, or that it is INCOMPLETE?
# A wrong case misleads a tester who runs it. A thin suite does not.
_ADV_P_SUGGESTION = 1  # names work not done, not a defect in work done
_ADV_P_CROSS_CASE = 2  # a relationship BETWEEN cases; each alone still runs
_ADV_P_UNEXECUTABLE = 3  # the case cannot be run -- visible on first attempt
_ADV_P_UNJUDGEABLE = 4  # the case runs but its outcome cannot be judged


# The example floor. Under budget pressure a bullet's rows shrink to this before
# any bullet is dropped, because a bullet's HEADLINE is the finding ("96 case(s)
# have an action the tester cannot execute as written") and its rows are EXAMPLES
# of it. A lost example costs the tester one tc_id they can re-derive from the
# suite; a lost headline costs them the knowledge that the problem exists.
# Examples are re-derivable, findings are not, so examples are spent first.
#
# Measured 2026-08-20 on an adversarial 96-case suite: at the full row cap the
# block kept ONE finding and dropped three; trimming rows instead keeps all four
# in 2114 bytes, under _BLOCK_CAP, so the drop path never runs at all.
#
# Measured 2026-09-06 over an 18-fixture matrix (4/10/16 bullets x 8/20 rows x
# 20/60/120-char rows), driving _bound_advisory_block itself:
#
#   * the floor was INERT above 4. The ladder's rungs were written out as a
#     literal ending (..., 4, _MIN_ROWS_SHOWN), and _trim_bullet_rows returns
#     the bullet unchanged when it already fits, so a floor of 5 trimmed
#     nothing the 4-rung had not already trimmed: floor 5 and floor 4 produced
#     BYTE-IDENTICAL output in all 18 fixtures. A constant that names itself
#     the floor while the ladder decides the real one is the same defect as a
#     sentinel only one consumer branches on.
#   * at 3 the floor stopped spending examples while findings were still being
#     dropped, which INVERTS the order of sacrifice this module declares above.
#     At 10 bullets of 60-char rows: 7 of 10 headlines survived at 3, all 10 at
#     2. At 120-char rows: 4 of 10 at 3, all 10 at 1.
#
# So the floor is 1 -- one example row per finding, every headline kept -- and
# _ROW_CAP_LADDER is DERIVED from it rather than written out beside it, so
# raising it takes effect instead of silently doing nothing.
_MIN_ROWS_SHOWN = 1


#: The trim ladder, derived so that _MIN_ROWS_SHOWN is always its last rung and
#: no rung ever sits at or below the floor. At the shipped floor this is
#: (12, 8, 6, 4, 3, 2, 1); at a floor of 5 it is (12, 8, 6, 5) -- binding in
#: both directions, which is what the literal could not do.
def _row_cap_ladder(floor: int) -> tuple[int, ...]:
    """The trim rungs for *floor*, ending at *floor* and never reaching it early.

    A free function rather than a comprehension evaluated once at import,
    because a ladder built at import can only ever be graded at the SHIPPED
    floor -- and the defect this replaced was invisible at the shipped floor
    and only appeared one value up. The test drives this at several floors.
    """
    return tuple(cap for cap in (12, 8, 6, 4, 3, 2) if cap > floor) + (floor,)


_ROW_CAP_LADDER = _row_cap_ladder(_MIN_ROWS_SHOWN)
_ROW_PREFIX = "  - "
_MORE_ROW_RE = re.compile(r"^  - \.\.\. and (\d+) more$")
