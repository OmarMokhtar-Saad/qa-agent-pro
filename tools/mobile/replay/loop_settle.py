"""Replay engine: what follows a device action (its error stop and its settle). Cut verbatim from tools/mobile/replay/replay.py (C5 w17)."""

from __future__ import annotations

from typing import Any, NamedTuple

from tools.mobile import actions as actions_mod
from tools.mobile import ime, step_timing
from tools.mobile.replay.context import STATUS_ERROR, _ReplayRun, _result, _Step
from tools.mobile.replay.device_ops import PASSWORD_REFUSAL_STATUS
from tools.mobile.replay.device_ops_consts import KEYBOARD_OPS as TEXT_OPS
from tools.mobile.replay.guard import _confirmed
from tools.mobile.replay.settle import SettleJob, SettleOpts, _settle
from tools.mobile.replay.steps_stop import StepAt, _close, _reply, _stop
from tools.mobile.replay.verdicts import Landing, _brought_to_front


class Acted(NamedTuple):
    """A device action done: its outcome, its trace entry and the screen in hand."""

    outcome: dict
    entry: dict
    screen: Any
    fresh: bool = False  # read AFTER the action: no redump needed


def _device_error_stop(run: _ReplayRun, acted: Acted) -> dict:
    """The reply for a device action that came back with an error."""
    outcome, entry, screen = acted.outcome, acted.entry, acted.screen
    if outcome.get("mask_text") and isinstance(entry.get("action"), dict):
        for key in ("text", "value"):
            if key in entry["action"]:
                entry["action"][key] = actions_mod.SECRET_MASK
    recoverable = bool(outcome.get("needs_model"))
    entry["outcome"] = "refused" if recoverable else "device_error"
    entry["detail"] = _confirmed(entry, str(outcome["error"]))[:400]
    at = StepAt(entry["index"], entry, screen)
    _close(run, at)
    status = PASSWORD_REFUSAL_STATUS if recoverable else STATUS_ERROR
    return _reply(_result(_stop(run, at, status)))


# A `launch` whose `am start` only brought this app's own task to
# the front of a screen that was ALREADY this app changed nothing a
# dump would see. The one redump it skips is the costliest call on a
# heavy AVD. ONLY the dump is skipped: `_settle` still runs on the
# screen in hand, so `no_change` and the system-dialog and
# left-app checks are kept.
def _nothing_actuated(step: _Step, acted: Acted) -> bool:
    """Whether the step touched nothing, so the screen in hand is still current.

    A wait only reads, and an optional tap that found nothing (its outcome
    says ``skipped``) never tapped: neither pays a settle dump.
    """
    if step.op in actions_mod.WAIT_OPS:
        return True
    skipped = (acted.outcome.get("content") or {}).get("skipped")
    return step.op == "tap_text_if_present" and bool(skipped)


def _front_landing(run: _ReplayRun, step: _Step, acted: Acted) -> bool:
    """Whether a ``launch`` only brought this app's own task to the front."""
    package = getattr(step.action, "package", None)
    landing = Landing(acted.outcome, acted.screen, run.ctx, package)
    return step.op == "launch" and _brought_to_front(landing)


def _extra_expected(step: _Step) -> tuple:
    """The package a ``launch`` is expected to land on, else nothing."""
    package = str(getattr(step.action, "package", "") or "")
    return (package,) if step.op == "launch" else ()


def _before_change_wait(run: _ReplayRun, step: _Step) -> bool:
    """True when the script's very next op observes the screen itself.

    Any wait (``wait_until_*``, ``wait``) and ``tap_text_if_present`` read the
    screen before they act, so an auto-settle dump after this step would only
    be a second read of the same moment.
    """
    at = int((getattr(step, "entry", None) or {}).get("index", -1)) + 1
    if not 0 < at < len(run.items):
        return False
    op = str(getattr(run.items[at], "op", "") or "")
    return op in actions_mod.WAIT_OPS or op == "tap_text_if_present"


def _next_op(run: _ReplayRun, step: _Step) -> str:
    """The op after *step* in the script, else ``""``."""
    at = int((getattr(step, "entry", None) or {}).get("index", -1)) + 1
    if not 0 < at < len(run.items):
        return ""
    return str(getattr(run.items[at], "op", "") or "")


def _next_typed_target(run: _ReplayRun, step: _Step) -> Any:
    """The target of the ``type`` that follows *step*, else ``None``.

    A tap before it only focuses the field when that field is already on the
    screen; the settle then has nothing to wait for (see ``settle_run``).
    """
    if _next_op(run, step) != "type":
        return None
    return getattr(run.items[int(step.entry["index"]) + 1], "target", None)


# A text step (``type``/``fill``/``clear``) the keyboard CONFIRMED and that
# another text step follows needs no re-dump: the keyboard itself proved the
# text landed (``landed == yes``) or the field cleared, and the next op resolves
# its target on the screen in hand and settles on its own read. A text step
# followed by a TAP keeps the re-dump on purpose: the tap's target (a Send
# control that only appears once text is in) and the destructive guard must see
# the actuated screen, not the one before the keyboard acted.
def _text_confirmed(step: _Step, acted: Acted) -> bool:
    """Whether the keyboard confirmed this text step."""
    content = acted.outcome.get("content") or {}
    if step.op == "clear":
        return content.get("cleared") is True
    return step.op in TEXT_OPS and content.get("landed") == ime.LANDED_YES


def _typed_in_place(run: _ReplayRun, step: _Step, acted: Acted) -> bool:
    """Whether a confirmed text step is followed by another text step."""
    return _text_confirmed(step, acted) and _next_op(run, step) in TEXT_OPS


def _device_opts(run: _ReplayRun, step: _Step, front: bool) -> SettleOpts:
    """The settle options of a device action; *front* skips the redump."""
    return SettleOpts(
        redump=not front,
        mark_no_change=True,
        op=step.op,
        extra_expected=_extra_expected(step),
        before_change_wait=_before_change_wait(run, step),
        next_target=_next_typed_target(run, step),
    )


async def _settle_device(
    run: _ReplayRun, step: _Step, acted: Acted
) -> tuple[Any, dict | None]:
    """Settle after a device action: ``(screen, reply)``; ``reply`` None = go on."""
    entry, screen = acted.entry, acted.screen
    front = _front_landing(run, step, acted) or acted.fresh
    front = front or _nothing_actuated(step, acted)
    typed = _typed_in_place(run, step, acted)
    if front or typed:
        step_timing.mark("dump_skipped")
    if step.op in actions_mod.MUTATING_OPS:
        job = SettleJob(run.ctx, entry, run.trace, entry["index"])
        opts = _device_opts(run, step, front or typed)
        opts = opts._replace(mark_no_change=not typed)  # not re-read != unchanged
        screen, stop = await _settle(job, screen, opts)
        if stop is not None:
            return screen, _reply(stop)
    _close(run, StepAt(entry["index"], entry, screen))
    return screen, None
