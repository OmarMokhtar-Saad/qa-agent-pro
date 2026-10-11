"""Replay engine: auto-settle and system-dialog handling. Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

import asyncio
import logging
import time

from tools.mobile import actions as actions_mod
from tools.mobile import (
    adb,
    dialog_handlers,
    dump_latency,
    latency_scale,
)
from tools.mobile.replay.context import (
    AUTO_SETTLE_MAX_S,
    DIALOG_SETTLE_S,
    MAX_DIALOG_TAPS,
    NO_AUTO_SETTLE_OPS,
    STATUS_ERROR,
    STATUS_NEEDS_MODEL,
    WAIT_POLL_S,
    Context,
    _append,
    _result,
    _sleep,
)
from tools.mobile.replay.dump import _drop_carried, _dump, _focus_probe, dump_raw
from tools.mobile.replay.elements import (
    _center,
    actuated_element,
    element_label,
    resolve_tap_text,
)
from tools.mobile.replay.guard import (
    MAX_GUARD_NODE_CHARS,
    destructive_hit,
    guard_detail,
    screen_hit,
)
from tools.mobile.replay.screen import (
    LEAVES_ON_PURPOSE,
    _screen_hash,
    _screen_package,
    _stamp_after,
    left_app_detail,
    system_dialog_detail,
    system_dialog_package,
)

logger = logging.getLogger("tools.mobile.executor")


def _unchanged(
    before_hash: str, after_hash: str, before_id: str, after_id: str
) -> bool:
    """Whether an action left the screen's CONTENT as it was.

    Judged on the content hash when BOTH ends have one. ``screen_id`` is the
    wrong instrument and was the one used: it is seeded from package, activity
    and the top THREE texts (``perception._screen_id``), deliberately, so a
    scrolled list stays the same screen -- and so a chat that received a whole
    new assistant reply reported ``no_change``. Measured on every send of
    ``mrun-20260907-174354-758700``, including the one the model then proved
    with an ``assert_pass``.

    With either hash missing the ID comparison is KEPT, which is exactly
    today's behaviour. ``no_change`` is what tells a model its tap did nothing,
    and dropping the signal on a screen that carries no hash would replace an
    occasionally coarse answer with no answer at all.

    ``assert screen_changed`` still reads the ID and stays documented as WEAK
    (``_evaluate_assert``): two consumers want two different questions answered,
    so they get two values with two names, not one value with a caveat. The
    destructive guard reads the ID and the elements too, and neither moves.
    """
    if before_hash and after_hash:
        return before_hash == after_hash
    return str(after_id) == str(before_id)


async def _auto_settle(ctx: Context, screen: object, before_hash: str) -> object:
    """Keep reading after an action whose first read showed no change.

    Owner decision (batch 3): poll every :data:`WAIT_POLL_S`. Once the content
    hash differs from *before_hash*, the screen is moving, and the read handed
    back is the first of two IDENTICAL reads in a row with the same focused
    activity -- a transition is not handed over mid-animation. A screen that
    never changes ends at :data:`AUTO_SETTLE_MAX_S` and the caller reports
    ``no_change`` exactly as before.

    Never raises and never fails the step: a dump error or a cut dump returns
    the latest good read, which is at worst the read the caller already had.
    Each dump is cut at the deadline, so the bound is the wall clock.
    """
    deadline = time.monotonic() + latency_scale.scale_settle(
        AUTO_SETTLE_MAX_S, dump_latency.p90_ms(ctx.serial)
    )
    current = screen
    moving = False
    last: tuple | None = None
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return current
        await _sleep(min(WAIT_POLL_S, left))
        if time.monotonic() >= deadline:
            return current
        dumped = await _dump(ctx, deadline=deadline)
        if dumped.get("error"):
            return current
        current = dumped.get("content")
        read_hash = _screen_hash(current)
        if not read_hash:
            return current
        if not moving:
            if read_hash == before_hash:
                continue
            moving = True
        key = (
            read_hash,
            await _focus_probe(ctx.serial, deadline - time.monotonic()),
        )
        if key == last:
            return current
        last = key


def _expected_here(ctx: Context, extra: object = ()) -> frozenset:
    """Packages the step expects in front besides the run's own app: the
    context's ``expect_apps`` plus *extra* (the package a ``launch`` just
    opened). Empty values are dropped, so a missing one widens nothing."""
    declared = (*tuple(getattr(ctx, "expect_apps", ()) or ()), *tuple(extra or ()))
    return frozenset(str(p) for p in declared if p)


def _cut_of(ctx: Context, screen: object, op: str, extra: object = ()) -> tuple:
    """``(outcome, package)`` for why the replay must stop on *screen*, or
    ``('', '')``. THE one place the system-dialog and left-the-app rules are
    decided, so the cut and the dialog answerer cannot disagree. A package in
    ``expect_apps`` (or *extra*) is not another app."""
    dialog = system_dialog_package(screen)
    if dialog:
        return "system_dialog", dialog
    package = _screen_package(screen)
    if (
        package
        and ctx.package
        and package != ctx.package
        and package not in _expected_here(ctx, extra)
        and str(op or "") not in LEAVES_ON_PURPOSE
    ):
        return "left_app", package
    return "", ""


def _cut_detail(kind: str, foreign: str, ctx: Context, entry: dict) -> str:
    """The refusal sentence for a cut, plus the guard's own sentence when a
    handler's tap was refused (so the model learns WHY nothing was answered)."""
    base = (
        system_dialog_detail(foreign)
        if kind == "system_dialog"
        else left_app_detail(foreign, ctx.package)
    )
    refused = str(entry.get("dialog_refused") or "")
    return (base + " " + refused).strip() if refused else base


def _dialog_element(action: object, screen: object) -> dict | None:
    """The pruned-screen element a handler's action names, or None when it is
    absent or ambiguous (no blind taps)."""
    if action.kind == dialog_handlers.TAP_TEXT:
        found = resolve_tap_text(action.value, screen)
        chosen = None if found.get("ambiguous") else found.get("element")
    elif action.kind == dialog_handlers.TAP_RID:
        try:
            target = actions_mod.Target(rid=action.value)
        except ValueError:
            return None
        resolved = (actions_mod.resolve_target(target, screen) or {}).get("content")
        chosen = (resolved or {}).get("element")
    else:
        chosen = None
    return chosen if isinstance(chosen, dict) else None


def _dialog_tap_refusal(
    ctx: Context, action: object, element: dict, screen: object
) -> str:
    """Why the destructive guard refuses a handler's tap on *element*, or ''.

    The SAME derivation a tester's tap is judged by (the named label, the
    actuated node on its own strings, then screen_hit on the judged node), and
    deliberately NOT gated on ``ctx.guard_destructive``: a handler is not the
    tester. ``_consume_confirm`` is never called -- a confirm is bound to the
    tester's own op fingerprint, so a handler can never spend one."""
    actuated = actuated_element(screen, _center(element))
    labels = [str(action.value)] if action.kind == dialog_handlers.TAP_TEXT else []
    labels.append(element_label(element, screen))
    if actuated is not None and actuated is not element:
        labels.append(element_label(actuated))
    judged = actuated if actuated is not None else element
    term = destructive_hit(" ".join(labels).strip())
    if not term:
        term = screen_hit(screen, "", judged=judged)
    return guard_detail(term, screen, ctx.package) if term else ""


async def _answer_one_dialog(
    ctx: Context, screen: object, foreign: str, extra: object
) -> dict | None:
    """Answer ONE interruption: ``None`` when nothing was done (no usable raw
    dump, no handler recognised it, the control is absent, the tap failed),
    ``{'refused': text}`` when the guard stopped the tap, else ``{'label',
    'screen'[, 'stop']}`` after the tap and a re-read."""
    raw = await dump_raw(ctx.serial)
    xml = raw.get("content") if isinstance(raw, dict) else None
    if not isinstance(xml, str) or not xml:
        return None
    expected = frozenset({ctx.package, *_expected_here(ctx, extra)}) - {""}
    action = dialog_handlers.handle(
        dialog_handlers.DialogContext(
            serial=ctx.serial, package=foreign, expected_apps=expected, xml=xml
        )
    )
    if action is None:
        return None
    back = action.kind == dialog_handlers.BACK
    point = None
    if not back:
        element = _dialog_element(action, screen)
        point = _center(element) if element is not None else None
        if point is None:
            return None
        refusal = _dialog_tap_refusal(ctx, action, element, screen)
        if refusal:
            return {"refused": refusal[:400]}
    _drop_carried(ctx.serial)
    if back:
        tapped = await adb.keyevent(ctx.serial, "KEYCODE_BACK")
    else:
        tapped = await adb.tap(ctx.serial, point[0], point[1])
    if tapped.get("error"):
        return None
    label = "back" if back else str(action.value)[:MAX_GUARD_NODE_CHARS]
    await asyncio.sleep(DIALOG_SETTLE_S)
    dumped = await _dump(ctx)
    if dumped.get("error"):
        return {"label": label, "screen": screen, "stop": True}
    return {"label": label, "screen": dumped.get("content")}


async def _answer_dialogs(
    ctx: Context, entry: dict, screen: object, op: str, extra: object = ()
) -> object:
    """Answer up to :data:`MAX_DIALOG_TAPS` interruptions and return the screen
    as it stands. ONE ``dialog_handlers.handle`` per interruption. Stops when
    the interruption is gone, nothing recognises it, or the guard refuses the
    tap (recorded as ``entry['dialog_refused']``). With no handler registered it
    does nothing at all, so a run that never registered one is unchanged."""
    if not dialog_handlers.handler_names():
        return screen
    answered: list = []
    for _ in range(MAX_DIALOG_TAPS):
        kind, foreign = _cut_of(ctx, screen, op, extra)
        if not kind:
            break
        step = await _answer_one_dialog(ctx, screen, foreign, extra)
        if step is None:
            break
        if step.get("refused"):
            entry["dialog_refused"] = step["refused"]
            break
        answered.append(step["label"])
        screen = step["screen"]
        if step.get("stop"):
            break
    if answered:
        entry["dialog_taps"] = answered
    return screen


async def _settle(
    ctx: Context,
    entry: dict,
    screen: object,
    trace: list[dict],
    index: int,
    *,
    redump: bool,
    mark_no_change: bool,
    op: str = "",
    extra_expected: tuple = (),
) -> tuple:
    """Re-read the screen after an action that could have changed it.

    THE ONE settle path, and it exists because there used to be none for
    ``wait``. ``wait`` is in ``actions.MUTATING_OPS``, but its branch returned
    before the re-dump that membership implied -- so a plain ``wait ms`` NEVER
    re-read the screen, and every assert after a wait was evaluated against the
    PRE-wait screen. An app's reply arrives during exactly that wait. It is why
    ``screen_changed`` was the only kind that appeared to work on the
    2026-09-04 live run: it compares ids rather than content.

    Returns ``(screen, stop)``. ``stop`` is a complete replay result when the
    replay must end here (and this function has already appended the entry),
    and ``None`` when the caller carries on and appends it itself.

    *mark_no_change* is False for ``wait``: ``no_change`` exists to tell a model
    "your tap did nothing", and a wait that changes nothing is the normal case.
    """
    if redump:
        screen, stop = await _settle_redump(ctx, entry, screen, trace, index)
        if stop is not None:
            return screen, stop
    # A registered dialog handler answers a known interruption BEFORE the screen
    # is stamped, so the entry records the screen the replay actually continues
    # on. No handler registered: this returns the screen untouched.
    screen = await _answer_dialogs(ctx, entry, screen, op, extra_expected)
    # BOTH halves (hash and id), through the one producer, never re-derived.
    _stamp_after(entry, screen)
    screen, unchanged = await _settle_unchanged(
        ctx, entry, screen, (redump, mark_no_change), op
    )
    if unchanged:
        entry["outcome"] = "no_change"
    kind, foreign = _cut_of(ctx, screen, op, extra_expected)
    if kind:
        return screen, _cut_stop(ctx, entry, screen, trace, (index, kind, foreign))
    return screen, None


async def _settle_redump(
    ctx: Context, entry: dict, screen: object, trace: list[dict], index: int
) -> tuple:
    """``_settle``'s re-dump half: ``(screen, stop)``, ``stop`` set on a failed dump."""
    dumped = await _dump(ctx)
    if dumped.get("error"):
        entry["outcome"] = "dump_failed"
        entry["detail"] = str(dumped["error"])[:400]
        _append(trace, entry)
        return screen, _result(STATUS_ERROR, trace, screen, "", entry["detail"], index)
    return dumped.get("content"), None


async def _settle_unchanged(
    ctx: Context, entry: dict, screen: object, flags: tuple, op: str
) -> tuple:
    """``(screen, unchanged)``: did the action change nothing, after auto-settle.

    *flags* is ``(redump, mark_no_change)``.
    """
    redump, mark_no_change = flags
    before_id = str(entry.get("before_screen_id") or "")
    before_hash = str(entry.get("before_screen_hash") or "")
    unchanged = mark_no_change and _unchanged(
        before_hash,
        entry["after_screen_hash"],
        before_id,
        entry["after_screen_id"],
    )
    # AUTO-SETTLE only on a read this call took itself: a skipped redump
    # (`launch` brought to front) already decided nothing moved, and a wait's
    # own poll (`mark_no_change=False`) is its own settle.
    if unchanged and redump and before_hash and str(op or "") not in NO_AUTO_SETTLE_OPS:
        screen = await _auto_settle(ctx, screen, before_hash)
        _stamp_after(entry, screen)
        unchanged = _unchanged(
            before_hash,
            entry["after_screen_hash"],
            before_id,
            entry["after_screen_id"],
        )
    return screen, unchanged


def _cut_stop(
    ctx: Context, entry: dict, screen: object, trace: list[dict], cut: tuple
) -> dict:
    """The replay result for an app that is not where the script thinks it is.

    *cut* is ``(index, kind, foreign)``. Recorded on the ACTION that took it
    there, and the replay stops: every target after this one would miss against
    a screen that belongs to somebody else, which is how one mistaken tap spent
    a case's three escapes and reported `blocked` without ever mentioning that
    the app had been left.
    """
    index, kind, foreign = cut
    entry["outcome"] = "system_dialog" if kind == "system_dialog" else "left_app"
    entry["detail"] = _cut_detail(kind, foreign, ctx, entry)
    _stamp_after(entry, screen)
    _append(trace, entry)
    return _result(STATUS_NEEDS_MODEL, trace, screen, "", entry["detail"], index)
