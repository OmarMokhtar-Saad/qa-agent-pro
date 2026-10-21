"""Replay waits: the text, element and idle condition wait step."""

from __future__ import annotations

import time

from tools.mobile.replay.context import (
    Context,
    ReplayStop,
    _append,
    _ReplayRun,
    _result,
)
from tools.mobile.replay.context_consts import STATUS_NEEDS_MODEL
from tools.mobile.replay.near_miss import near_miss
from tools.mobile.replay.screen import _stamp_after
from tools.mobile.replay.settle_mark import SettleJob, SettleOpts
from tools.mobile.replay.settle_perm import mark_unclear
from tools.mobile.replay.settle_run import _settle
from tools.mobile.replay.wait_inprogress import note_in_progress
from tools.mobile.replay.wait_stall import note_stalled
from tools.mobile.replay.waits_poll import _wait_until_element
from tools.mobile.replay.waits_prior import note_gone
from tools.mobile.replay.waits_refuse import refusal, stamp_refused
from tools.mobile.replay.waits_spec import _condition_words, stamp_early
from tools.mobile.replay.waits_text import _wait_until_idle, _wait_until_text
from tools.mobile.replay.waits_types import (
    CondStep,
    ElementWait,
    TextWait,
    WaitSpec,
    WaitView,
)
from tools.mobile.replay.waits_visual import with_source


def _is_gone(action: object, kind: str) -> bool:
    """Whether the condition was for something to disappear."""
    return kind == "gone" or bool(getattr(action, "until_gone", False))


def _element_want(action: object, spec: WaitSpec) -> ElementWait:
    """The element wait an ``until_rid`` action asks for."""
    rid_text = str(getattr(action, "until_rid_text", "") or "")
    gone = bool(getattr(action, "until_gone", False))
    return ElementWait(spec[1], rid_text, gone, spec[2])


async def poll_into(run: _ReplayRun, step: CondStep, screen: object) -> tuple:
    """:func:`_poll_condition`, plus what pop-ups answered meanwhile onto the entry."""
    action, spec, entry = step
    polled = await _poll_condition(WaitView(run.ctx, screen), action, spec)
    entry.update(run.ctx.wait_dialog)
    return polled


async def _poll_condition(view: WaitView, action: object, spec: WaitSpec) -> tuple:
    """Poll one wait condition: ``(found, screen, interruption)``.

    *spec* is ``(kind, wait_text, wait_ms)`` from ``_wait_spec``. The sentence a
    watched interruption left in ``ctx.wait_interruption`` is cleared before the
    poll and read (and cleared) after it.
    """
    ctx, screen = view
    kind, wait_text, wait_ms = spec
    ctx.wait_interruption, ctx.wait_dialog = "", {}
    if kind == "element":
        want = _element_want(action, spec)
        found, screen, _timed_out = await _wait_until_element(ctx, screen, want)
    elif kind == "idle":
        found, screen, _timed_out = await _wait_until_idle(ctx, screen, wait_ms)
    else:
        text_want = TextWait(wait_text, wait_ms, kind == "gone")
        found, screen, _timed_out = await _wait_until_text(ctx, screen, text_want)
    interruption, ctx.wait_interruption = ctx.wait_interruption, ""
    return found, screen, interruption


async def _settle_met(run: _ReplayRun, step: CondStep, screen: object) -> tuple:
    """Settle a met condition wait: ``(screen, stop)``, without a re-dump."""
    action, _spec, entry = step
    op = str(getattr(action, "op", "") or "")
    job = SettleJob(run.ctx, entry, run.trace, entry["index"])
    # No re-dump: the poll already left a fresh screen.
    return await _settle(
        job, screen, SettleOpts(redump=False, mark_no_change=False, op=op)
    )


def _record_miss(entry: dict, interruption: str, missed: str) -> None:
    """Stamp a wait that never met its condition: interrupted, else timed out."""
    if interruption:
        entry["outcome"] = "interrupted"
        entry["detail"] = interruption
    else:
        entry["outcome"] = "wait_timeout"
        entry["detail"] = missed


def _miss(entry: dict, words: tuple, timing: tuple) -> None:
    """:func:`_record_miss`, plus ``wait_early`` when the wait ended early."""
    _record_miss(entry, *words)
    mark_unclear(entry)
    started, wait_ms = timing
    stamp_early(entry, int((time.monotonic() - started) * 1000), wait_ms)


def needs_model_reply(run: _ReplayRun, screen: object, entry: dict) -> dict:
    """The reply that hands a missed wait back to the model."""
    if not note_stalled(run.ctx, entry, screen):
        note_in_progress(entry, screen)
    stop = ReplayStop(
        STATUS_NEEDS_MODEL, run.trace, screen, "", entry["detail"], entry["index"]
    )
    return {"error": None, "content": _result(stop)}


def _words_near(action: object, spec: WaitSpec, screen: object) -> tuple:
    """:func:`_condition_words`, its miss sentence naming a substring-only label
    (``setting`` on ``Settings``): a text wait never meets on one (D12)."""
    met, missed, label = _condition_words(action, spec[0], spec[1])
    extra = near_miss(screen, spec[1]) if spec[0] == "text" else ""
    return met, missed + extra, label


def _miss_words(interruption: str, label: str) -> str:
    """The interruption sentence with the wait it cut short, or ``""``."""
    return interruption and interruption + " (while " + label + ")"


def _pair(ctx: Context, words: tuple) -> tuple:
    """``(interruption sentence, timeout sentence naming the sources read)``."""
    interruption, label, missed = words
    return _miss_words(interruption, label), with_source(ctx, missed)


async def _on_met(run: _ReplayRun, step: CondStep, met: tuple) -> tuple:
    """Record a met condition and settle: ``(screen, stop reply or None)``."""
    action, spec, entry = step
    screen, words = met
    entry["outcome"], entry["detail"] = "ok", words
    if _is_gone(action, spec[0]):
        note_gone(run.ctx)
    screen, stop = await _settle_met(run, step, screen)
    return screen, (None if stop is None else {"error": None, "content": stop})


def _refused(run: _ReplayRun, step: CondStep, seen: tuple) -> tuple:
    """Hand an impossible wait back to the model without polling it."""
    screen, reason = seen
    entry = step[2]
    stamp_refused(entry, reason)
    _stamp_after(entry, screen)
    _append(run.trace, entry)
    return screen, needs_model_reply(run, screen, entry)


async def _condition_wait(run: _ReplayRun, step: CondStep, screen: object) -> tuple:
    """A wait on a text, element or idle condition: ``(screen, reply)``.

    ``reply`` is ``None`` when the condition was met and the replay goes on.
    """
    action, spec, entry = step
    if reason := refusal(run.ctx, step, screen):
        return _refused(run, step, (screen, reason))
    started = time.monotonic()
    found, screen, interruption = await poll_into(run, step, screen)
    met, missed, label = _words_near(action, spec, screen)
    if found:
        screen, stop = await _on_met(run, step, (screen, met))
        if stop is not None:
            return screen, stop
    else:
        _miss(entry, _pair(run.ctx, (interruption, label, missed)), (started, spec[2]))
    _stamp_after(entry, screen)
    _append(run.trace, entry)
    return screen, (None if found else needs_model_reply(run, screen, entry))
