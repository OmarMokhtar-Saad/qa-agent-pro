"""The intake questions, their cap and the reset-shaped goal hint."""

from __future__ import annotations

from typing import Any

from tools.mobile.charter_coerce import _unset
from tools.mobile.charter_consts import MAX_QUESTIONS
from tools.mobile.charter_schema import DEPTHS, DESTRUCTIVE

#: The server's question set. FIVE, no more, and the field it does NOT ask --
#: ``stop_on`` -- is the one with an answer that is safe and complete without
#: asking (stop when the budget runs out). It is taken from a charter that
#: volunteers it and is otherwise defaulted and NAMED in the report.
QUESTIONS: tuple[dict[str, Any], ...] = (
    {
        "field": "goal",
        "ask": "What should the app be made to do? One sentence.",
        "options": (),
    },
    {
        "field": "depth",
        "ask": "Should this run try the happy path, try to break things, or both?",
        "options": DEPTHS,
    },
    {
        "field": "scope",
        "ask": (
            "Any screens or flows to stay inside, and any to stay out of? "
            "Answer as include and exclude lists; empty means the whole app."
        ),
        "options": (),
    },
    {
        "field": "destructive",
        "ask": (
            "May the run take actions that change data -- none, reversible "
            "ones only, or all of them once the tester has said yes? With "
            "`none`, which is also what an unanswered charter gets, a stopped "
            "action ENDS the run rather than pausing for you; ask for "
            "`reversible` if you would rather be asked and carry on."
        ),
        "options": DESTRUCTIVE,
    },
    {
        "field": "budget",
        "ask": "How many steps and how many minutes may this run spend?",
        "options": (),
    },
)

#: The fields a question exists for. Derived, never listed twice.
ASKABLE = tuple(question["field"] for question in QUESTIONS)


#: Reset-shaped goals get a NAMED hint toward `destructive: "reversible"`
#: rather than a silent default flip. Flipping the shipped default risks a
#: false positive on a free-text goal that only mentions a reset in passing,
#: and this module's own rule is that a REDUCTION says so by name in the
#: reply -- an unset field pointed at a destructive-shaped goal is the same
#: kind of gap in the other direction, so it gets the same treatment: named,
#: not silently assumed either way. Audit item 4.
RESET_HINT_WORDS = (
    "reset",
    "clear data",
    "clear app data",
    "fresh install",
    "reinstall",
    "wipe",
)


# "" unless *raw* left `destructive` unset AND *goal* names a reset.
#
# Never changes the charter: `defaults()` above still ships
# `destructive: "none"`, which REFUSES every destructive op. This is a
# disclosure only, so a tester sees -- before the run starts, not after
# its first refused step -- that a goal asking to reset/clear/reinstall
# will refuse its own first action unless the next call also sends
# `destructive: "reversible"`. Read off *raw*, the charter AS SENT, for
# the same M11 reason `describe_terms` reads off `charter` and never
# `terms`.
def destructive_hint(raw: object, goal: object) -> str:
    """The "reset goal, destructive unset" hint, else ""."""
    body = raw if isinstance(raw, dict) else {}
    if str(body.get("destructive") or "").strip():
        return ""
    text = str(goal or "").lower()
    if not any(word in text for word in RESET_HINT_WORDS):
        return ""
    return (
        "This goal reads like it wants a reset, but no `destructive` was "
        'sent, so the charter defaults to `destructive: "none"` and every '
        "irreversible action REFUSES. Start again with "
        '`destructive: "reversible"` if the reset should actually run.'
    )


# The questions still outstanding, NEVER more than :data:`MAX_QUESTIONS`.
#
# Outstanding is judged by :func:`_unset` against what the caller actually
# sent, so a field answered WITH its default value is not asked again.
#
# **HAND THIS THE RAW CHARTER, NEVER A NORMALISED ONE.** Measured: this
# function returns FIVE questions for ``{}`` and ONE for ``normalize({})``,
# because normalisation fills every field and four of the five then read as
# ANSWERED. Its only production caller,
# ``tools.mobile.run_packets.build_charter_intake``, passed the normalised charter,
# so a tester with no charter was asked ONE question and the other four were
# silently defaulted -- the absence of the feature wearing the feature's
# name. The function itself was graded (M2d) and the WIRING was not; the
# seam is now graded by
# ``test_an_empty_intake_asks_all_five_questions_through_the_builder``
# (mutant M1c), which asserts ``== 5`` and not ``<= 5``, because an upper
# bound is satisfied by one and that is exactly how this hid.
#
# The slice is the cap made unbypassable: a caller cannot ask for six by
# handing this an emptier charter. ``QUESTIONS`` has exactly five entries
# today, so the slice is inert in every state the shipped module can reach;
# the only world in which it binds is one where someone appends a SIXTH
# question, and ``test_the_slice_still_holds_with_a_sixth_question`` builds
# exactly that world by monkeypatching ``QUESTIONS`` to six entries (mutant
# M1b). Without that pin nothing would grade this line -- deleting the slice
# survives the suite, measured.
def questions_for(charter: object) -> tuple:
    """The questions still outstanding, never more than MAX_QUESTIONS."""
    body = charter if isinstance(charter, dict) else {}
    out = [question for question in QUESTIONS if _unset(body, question["field"])]
    return tuple(out[:MAX_QUESTIONS])
