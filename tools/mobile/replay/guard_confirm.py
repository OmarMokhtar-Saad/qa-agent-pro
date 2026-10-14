"""Replay guard confirm: spending the one-shot confirmation on the stop it was given for."""

from __future__ import annotations

from typing import NamedTuple

from tools.mobile import perception
from tools.mobile.replay.context_ctx import Context
from tools.mobile.replay.guard_consts import (
    CONFIRM_HINT,
    CONFIRM_MISMATCH_HINT,
    MAX_GUARD_NODE_CHARS,
)
from tools.mobile.replay.guard_lex import _control_detail
from tools.mobile.replay.guard_screen import guard_detail


def _guard_node(label: object) -> str:
    """The stopped node as the confirm fingerprint and the host see it.

    ``SEGMENT_BREAK`` is an internal seam, so it leaves as a space: the host
    never gets a control character, and a stop fingerprinted before the seam
    existed still matches.
    """
    return str(label).replace(perception.SEGMENT_BREAK, " ")[:MAX_GUARD_NODE_CHARS]


class ConfirmArgs(NamedTuple):
    hit: str
    screen: object
    entry: dict
    op: str
    label: object


class ConfirmHint(NamedTuple):
    op: str
    hit: str
    mismatched: bool = False


def _matches_prior(ctx: Context, args: ConfirmArgs) -> bool:
    stopped = getattr(ctx, "prior_guard_stop", None)
    if stopped is None:
        return True
    current = {
        "term": str(args.hit)[:80],
        "op": str(args.op)[:40],
        "node": _guard_node(args.label),
    }
    return bool(stopped) and stopped == current


# Spend the one-shot ``confirm_destructive`` on THIS control stop, or not.
#
# Only a CONTROL stop (the :data:`GUARD_DETAIL` wording) can be confirmed; a
# screen-level stop names a different move, and ``guard_refuses`` outranks
# any confirm. Bound to the op the guard stopped on the PRIOR turn, not any
# control: ``ctx.prior_guard_stop`` carries that stop's fingerprint
# (``None`` means untracked, the lenient default every direct
# ``Context(...)`` unit test still gets; ``{}`` means tracked and no stop
# was recorded). A tracked confirm that does not match refuses by name and
# leaves the confirm UNSPENT, so a later, correctly-aimed resubmission can
# still use it. The trace entry records what was confirmed, so the run
# folder's checkpoint of this turn names the op and the term.
#
# *args* carries the stop: its term, screen, trace entry, the op that stopped
# and the control's label.
def _consume_confirm(ctx: Context, args: ConfirmArgs) -> bool:
    """Spend the one-shot ``confirm_destructive`` on THIS control stop, or not."""
    if not ctx.confirm_destructive or bool(getattr(ctx, "guard_refuses", False)):
        return False
    if guard_detail(args.hit, args.screen, ctx.package) != _control_detail(args.hit):
        return False
    if not _matches_prior(ctx, args):
        args.entry["guard_confirm_mismatch"] = True
        return False
    ctx.confirm_destructive = False
    args.entry["guard_confirmed"] = {"op": args.op, "term": str(args.hit)[:80]}
    return True


def _with_confirm_hint(detail: str, hint: ConfirmHint) -> str:
    """:data:`CONFIRM_HINT` on a control stop; any other stop is unchanged."""
    if detail != _control_detail(hint.hit):
        return detail
    text = CONFIRM_MISMATCH_HINT if hint.mismatched else CONFIRM_HINT
    return detail + text % (hint.op, repr(str(hint.hit)[:60]))


def _confirmed(entry: dict, detail: str) -> str:
    """Prefix the detail of an op that ran on a confirmed destructive stop."""
    confirmed = entry.get("guard_confirmed")
    if not isinstance(confirmed, dict):
        return detail
    return (
        "CONFIRMED destructive op ("
        + str(confirmed.get("op") or "")
        + " on "
        + repr(str(confirmed.get("term") or ""))
        + "): "
        + detail
    ).strip()
