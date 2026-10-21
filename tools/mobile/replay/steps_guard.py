"""Replay engine: the destructive guard's stop and its trace entry. Cut verbatim from tools/mobile/replay/steps.py (C5 w17)."""

from __future__ import annotations

from typing import NamedTuple

from tools.mobile.replay.context import (
    STATUS_ERROR,
    STATUS_NEEDS_TESTER,
    _ReplayRun,
    _result,
    _Step,
)
from tools.mobile.replay.guard import (
    GUARD_DETAIL_REFUSED,
    ConfirmArgs,
    ConfirmHint,
    _consume_confirm,
    _guard_node,
    _with_confirm_hint,
    destructive_hit,
    guard_detail,
    screen_hit,
)
from tools.mobile.replay.steps_label import _guard_label
from tools.mobile.replay.steps_stop import StepAt, _close, _reply, _stop
from tools.mobile.replay.unlabeled_tap import names_target, unlabeled_hit


class Tripped(NamedTuple):
    """What the guard tripped on: the op and the destructive term it hit."""

    op: str
    term: str


# WHAT the guard stopped is decided in ``_guard_stop`` and is not touched
# here. This decides only what happens AFTER the hit, which is the one thing
# a charter's ``destructive`` field is allowed to decide. The reply that
# carries the term stays in ``_guard_stop``, the reviewed sink.
#
# ONE call, not a chain. The chain that was here handled two
# sentinels and gave every future one the control wording --
# the same half-wiring at the other consumer is what round 3
# fixed, and this site still had it.
def _record_guard_stop(run: _ReplayRun, at: StepAt, tripped: Tripped) -> bool:
    """Write the trace entry of a guard stop and say whether the run refuses."""
    entry = at.entry
    entry["outcome"] = "guard_stop"
    refuses = bool(getattr(run.ctx, "guard_refuses", False))
    mismatched = bool(entry.get("guard_confirm_mismatch"))
    entry["detail"] = (
        GUARD_DETAIL_REFUSED
        if refuses
        else _with_confirm_hint(
            guard_detail(tripped.term, at.screen, run.ctx.package),
            ConfirmHint(tripped.op, tripped.term, mismatched),
        )
    )
    _close(run, at)
    return refuses


# --- _guard_stop: why the destructive guard is shaped this way
#
# DENY BY DEFAULT, and judge the node the action ACTUATES, not the node it
# NAMES. Both halves were learned by shipping the failure.
#
# The op half: this was an allow-list of ops believed to tap, and it was
# wrong twice -- 'clear' taps to focus the field, and a TARGETED 'scroll'
# swipes on the resolved element. So every op is guarded unless it is
# declared inert in NON_ACTUATING_OPS.
#
# The node half: the guard judged the RESOLVED element, and Android does not
# deliver a touch to the resolved element. A tap on a non-clickable child
# goes to the nearest CLICKABLE ancestor, so `tap {"text": "OK"}` on the
# child of a `btn_delete_account` wrapper reached the device while `tap
# {"rid": ...}` on the wrapper itself was stopped; and a targeted swipe
# re-centred only its X, so `scroll left` naming a benign row swiped across
# "Slide to confirm payment" at the table's Y. Measured through
# adb._run_argv, both. So the guard ALSO judges `actuated_element` at the
# very point `_perform` will touch, and a targeted swipe is confined to the
# element it names (see `_swipe_points`).
#
# The property this buys: acting on X through a child gets the SAME verdict
# as acting on X directly, so no new false-positive class -- a wrapper that
# stops a tap on its child already stopped a tap on itself.
#
# A target-less pan names nothing and is judged on nothing. THE RESIDUAL,
# stated: the finger starts at the table point, and a slide-to-confirm
# control lying there would be actuated. Widening to that point would stop
# every pan over a transactions list whose row reads "Transfer to ...", and
# a guard stop is terminal for a scripted case, so the pan stays unjudged and
# the decision is pinned in both directions by tests/mobile.
#
# The `press` branch: the key fires the FORM's IME action, and no dump names
# the control that action reaches (uiautomator emits no imeOptions). The
# actuated node is unknown BY CONSTRUCTION, so the scope is every element the
# packet carries -- and a packet that does not carry the whole screen cannot
# clear it. Measured before this line existed: `tap {"text": "Confirm
# payment"}` was refused while `press enter {"rid": amount}` on the same form
# sent KEYCODE_ENTER.
#
# The judged-node branch runs LAST, and only with a node in hand: a path that
# has a term today keeps it, and `judged=None` can never reach the fidelity
# branch, so nothing else about this guard moves.
#
# The refused reply: EXPLICIT KEYWORDS, never a dict splat. The sentinel
# ratchet forbids ``**kwargs`` into this sink because ``guard_term``'s
# provenance must be traceable by reading the call, and it is right: a splat
# hides which keys reach the payload. ``guard_refused`` is an EXPLICIT marker
# rather than a comparison against the detail PROSE -- a reader that matches
# on wording breaks the moment the wording is improved, and the wording is the
# part the tester actually reads.
def _guard_stop(run: _ReplayRun, step: _Step, at: StepAt) -> dict | None:
    """The reply that ends the replay at the guard, else ``None`` (why: above)."""
    ctx, op, entry, screen = run.ctx, step.op, step.entry, at.screen
    label, judged_node = _guard_label(step, screen)
    hit = destructive_hit(label)
    if not hit and not names_target(step.element, screen):  # see unlabeled_tap
        hit = unlabeled_hit(op, judged_node, screen)
    if not hit and op == "press":  # IME action: see the `press` branch above
        hit = screen_hit(screen, ctx.package)
    if not hit and judged_node is not None:  # LAST: see the judged-node branch
        hit = screen_hit(screen, "", judged=judged_node)
    args = ConfirmArgs(hit, screen, entry, op, label)
    if not hit or _consume_confirm(ctx, args):
        return None
    if _record_guard_stop(run, at, Tripped(op, hit)):
        stop = _stop(run, at, STATUS_ERROR)
        return _reply(_result(stop, guard_term=hit, guard_refused=True))
    stop = _stop(run, at, STATUS_NEEDS_TESTER)
    node = _guard_node(label)
    return _reply(_result(stop, guard_term=hit, guard_op=op, guard_node=node))
