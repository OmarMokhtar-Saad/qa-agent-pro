"""Which expected result belongs to which step of an imported case."""

from __future__ import annotations

from tools.mobile.imp_consts import TRAILING_EXPECTED_NOTE, UNCOVERED_STEP_NOTE


def _single(index: int, total: int, expected: list[str]) -> str:
    # The documented accommodation, and the ONLY coherent reading of it:
    # the one stated outcome belongs to the LAST step. The old code reached
    # its ``index < len(expected)`` fallback first, so step 1 AND the last
    # step both claimed this text while the steps between them carried the
    # placeholder -- the same assertion attached twice, which reads to a
    # tester as two separate verifications of one outcome.
    return expected[0] if index == total - 1 else TRAILING_EXPECTED_NOTE


def _surplus(index: int, total: int, expected: list[str]) -> str:
    # MORE outcomes than steps. This used to fall through to the partial-list
    # branch below and return expected[0], which was doubly wrong: the
    # surplus outcomes vanished with no entry in `rejected` and no
    # `truncated` flag, and the step asserted the FIRST outcome rather than
    # the one the case was written to verify. Silent loss of
    # tester-authored content is the one thing this module promises not to
    # do (see the module docstring).
    #
    # Folding the tail onto the last step keeps every outcome the tester
    # wrote and keeps them in order. Rejecting the whole row was the other
    # candidate and is worse for a habit this ordinary: a tester who writes
    # one prose step and three numbered outcomes has said something
    # perfectly clear.
    if index == total - 1:
        return "; ".join(expected[index:])
    return expected[index]


def _partial(index: int, expected: list[str]) -> str:
    # A partial list (1 < len < total) aligns from the START, which is the only
    # sane reading of "they wrote the first few and stopped"; the uncovered
    # tail carries the pointer rather than a borrowed assertion.
    if index < len(expected):
        return expected[index]
    return UNCOVERED_STEP_NOTE


# Four shapes, all four stated because the fourth was missing and wrong:
# counts equal, ONE outcome, a partial list, and MORE outcomes than steps
# (folded onto the last step -- never dropped).
#
# Aligned one-to-one when the counts match. When a row carries FEWER expected
# results than steps -- overwhelmingly the common case, because a tester writes
# "do this, then this, then this, and X should happen" -- the LAST expected
# result lands on the LAST step and the earlier steps carry an explicit,
# honest pointer. It is a placeholder, and it says so; it is not a fabricated
# assertion, and no step is silently given a result it does not have.
def _expected_for(index: int, expected: list[str], total: int) -> str:
    """Which expected result belongs to step *index*."""
    if not expected:
        return ""
    if len(expected) == total:
        return expected[index]
    if len(expected) == 1:
        return _single(index, total, expected)
    if len(expected) > total:
        return _surplus(index, total, expected)
    return _partial(index, expected)
