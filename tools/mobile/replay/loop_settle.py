"""Replay engine: what follows a device action (its error stop and its settle). Cut verbatim from tools/mobile/replay/replay.py (C5 w17)."""

from __future__ import annotations

from typing import Any, NamedTuple

from tools.mobile import actions as actions_mod
from tools.mobile import step_timing
from tools.mobile.replay.context import STATUS_ERROR, _ReplayRun, _result, _Step
from tools.mobile.replay.device_ops import PASSWORD_REFUSAL_STATUS
from tools.mobile.replay.guard import _confirmed
from tools.mobile.replay.settle import SettleJob, SettleOpts, _settle
from tools.mobile.replay.steps_stop import StepAt, _close, _reply, _stop
from tools.mobile.replay.verdicts import Landing, _brought_to_front


class Acted(NamedTuple):
    """A device action done: its outcome, its trace entry and the screen in hand."""

    outcome: dict
    entry: dict
    screen: Any


def _device_error_stop(run: _ReplayRun, acted: Acted) -> dict:
    """The reply for a device action that came back with an error."""
    outcome, entry, screen = acted
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
def _front_landing(run: _ReplayRun, step: _Step, acted: Acted) -> bool:
    """Whether a ``launch`` only brought this app's own task to the front."""
    package = getattr(step.action, "package", None)
    landing = Landing(acted.outcome, acted.screen, run.ctx, package)
    return step.op == "launch" and _brought_to_front(landing)


def _extra_expected(step: _Step) -> tuple:
    """The package a ``launch`` is expected to land on, else nothing."""
    package = str(getattr(step.action, "package", "") or "")
    return (package,) if step.op == "launch" else ()


def _device_opts(step: _Step, front: bool) -> SettleOpts:
    """The settle options of a device action; a front landing skips the redump."""
    return SettleOpts(
        redump=not front,
        mark_no_change=True,
        op=step.op,
        extra_expected=_extra_expected(step),
    )


async def _settle_device(
    run: _ReplayRun, step: _Step, acted: Acted
) -> tuple[Any, dict | None]:
    """Settle after a device action: ``(screen, reply)``.

    ``reply`` is ``None`` when the replay goes on to the next action.
    """
    front, entry, screen = _front_landing(run, step, acted), acted.entry, acted.screen
    if front:
        step_timing.mark("dump_skipped")
    if step.op in actions_mod.MUTATING_OPS:
        job = SettleJob(run.ctx, entry, run.trace, entry["index"])
        screen, stop = await _settle(job, screen, _device_opts(step, front))
        if stop is not None:
            return screen, _reply(stop)
    _close(run, StepAt(entry["index"], entry, screen))
    return screen, None
