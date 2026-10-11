"""Replay engine: screen dumps, carried-dump reuse and activity resolution. Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import logging
import time

from tools.mobile import (
    adb,
    dump_latency,
    latency_scale,
    step_timing,
    tree_optin,
    tree_source,
)
from tools.mobile.providers import composite
from tools.mobile.replay.context import Context

logger = logging.getLogger("tools.mobile.executor")


async def dump_raw(serial: str, *, use_provider: bool = True) -> dict:
    """ONE raw ``uiautomator dump`` for a caller outside :func:`_dump`.

    Session open, the case lane's foreground loop and the explore turn read
    the raw dump themselves; the a11y parity check passes ``use_provider=False``
    because it needs uiautomator's own answer, not the fast dump it is judging. Calling ``adb.uiautomator_dump`` directly would
    start a second dump beside one :func:`_dump` left running after a cut, on
    the same ``adb.DUMP_REMOTE_PATH`` (fix round 3, item 4). This takes the
    same guard: join the dump in flight, start this one through
    ``dump_latency.start`` (so its real time feeds the p90) and await it
    shielded, so a caller's own ``wait_for`` cancels the wait, never the dump.
    """
    await dump_latency.join(serial)
    task = dump_latency.start(
        serial, adb.uiautomator_dump(serial, use_provider=use_provider)
    )
    return await asyncio.shield(task)


#: What a dump cut at a wait's deadline reports. An error result, because the
#: polled wait already treats any failed poll as "stop polling"; the dump
#: itself was NOT cancelled and finishes on the device (see `_dump`).
DUMP_CUT_DETAIL = (
    "The screen dump did not finish before the wait's deadline; it was left "
    "to finish on the device."
)


#: How old a carried screen may be and still stand for the next call's first
#: read. The tester can touch the phone between calls, so this is short.
REUSE_MAX_AGE_S = 15.0

#: Devices whose last screen is carried; the least recently read is dropped.
MAX_CARRIED_SERIALS = 16

#: Seconds the live focus probe may take when confirming a carried screen.
REUSE_PROBE_S = 3.0

#: What a tree source may fail with. CancelledError is not here.
SOURCE_FAILURES = (OSError, ValueError, RuntimeError, asyncio.TimeoutError)


@dataclasses.dataclass(frozen=True)
class _Carried:
    """The last observed screen of one device and what it was read against."""

    result: dict
    taken: float
    package: str
    activity: str


_CARRIED: dict = {}


def _drop_carried(serial: str) -> None:
    """Forget the carried screen: something that can change it is about to run."""
    _CARRIED.pop(str(serial or ""), None)


def _remember(ctx: Context, observed: object) -> None:
    """Keep a successful observation for the next call when the context allows."""
    if not ctx.carry_screen:
        return
    content = observed.get("content") if isinstance(observed, dict) else None
    if not isinstance(content, dict) or observed.get("error"):
        _drop_carried(ctx.serial)
        return
    key = str(ctx.serial or "")
    _CARRIED.pop(key, None)
    _CARRIED[key] = _Carried(
        copy.deepcopy(observed),
        time.monotonic(),
        str(content.get("package") or ""),
        str(content.get("activity") or ""),
    )
    while len(_CARRIED) > MAX_CARRIED_SERIALS:
        del _CARRIED[next(iter(_CARRIED))]


async def _take_carried(ctx: Context) -> dict | None:
    """The carried screen when it is still safe to treat as a fresh read.

    Used at most once (it is popped), and only when it is young, belongs to the
    run's package, and the device's LIVE focus still equals the activity it was
    read on. An unknown focus is not a match. Any actuating op, and the keyboard
    swaps, drop it before this is reached.
    """
    if not ctx.carry_screen:
        return None
    held = _CARRIED.pop(str(ctx.serial or ""), None)
    if held is None or time.monotonic() - held.taken > REUSE_MAX_AGE_S:
        return None
    if ctx.package and held.package != ctx.package:
        return None
    focus = await _focus_probe(ctx.serial, REUSE_PROBE_S)
    if not focus or focus != held.activity:
        return None
    step_timing.mark("dump_carried")
    return held.result


async def _first_screen(ctx: Context) -> dict:
    """The first read of a call: the carried screen if valid, else a dump."""
    carried = await _take_carried(ctx)
    if carried is not None:
        return carried
    return await _dump(ctx)


async def _pick_source(serial: str) -> object:
    try:
        return await tree_source.select_tree_source(serial, tree_optin.allowed)
    except SOURCE_FAILURES:
        logger.warning("mobile.executor: tree source selection failed", exc_info=True)
        return None


async def _raw_dump(serial: str) -> dict:
    """ONE raw dump through the selected tree source; the adb dump on any miss.

    Only a source ``tree_optin.allowed`` lets through is ever tried, so without
    the tester's consent this is exactly the adb dump. A source that returns
    nothing or raises falls back to the adb dump in the same call.
    """
    source = await _pick_source(serial)
    if source is not None and source.name != tree_source.FALLBACK_SOURCE:
        timeout = latency_scale.scale_timeout(
            latency_scale.TREE_TIMEOUT_S, dump_latency.p90_ms(serial)
        )
        try:
            result = await source.dump(serial, timeout)
        except SOURCE_FAILURES:
            logger.warning("mobile.executor: tree source failed", exc_info=True)
            result = None
        if result is not None and result.xml:
            step_timing.note_source(result.source)
            return {"error": None, "content": result.xml}
    step_timing.note_source(tree_source.FALLBACK_SOURCE)
    return await adb.uiautomator_dump(serial)


async def _dump(ctx: Context, deadline: float | None = None) -> dict:
    """Re-dump and re-prune. Returns the ``{"error","content"}`` shape.

    SINGLE-FLIGHT PER SERIAL (fix round 3, item 4). Every dump writes the same
    ``adb.DUMP_REMOTE_PATH``, so this first joins any dump still running on
    the device and only then starts its own, through ``dump_latency.start``,
    which also records the dump's real time for the adaptive margin.

    With a *deadline* (an instant on this module's ``time.monotonic``) the
    wait is cut there -- and ONLY the wait: ``asyncio.shield`` leaves the adb
    dump running to its end, where it records its time and the next ``_dump``
    joins it. A cut returns :data:`DUMP_CUT_DETAIL` as an error.
    """
    serial = ctx.serial
    if deadline is None:
        await dump_latency.join(serial)
    elif not await dump_latency.join(serial, deadline - time.monotonic()):
        step_timing.mark("dump_cut")
        return {"error": DUMP_CUT_DETAIL, "content": None}
    task = dump_latency.start(serial, _raw_dump(serial))
    try:
        if deadline is None:
            raw = await step_timing.timed("ui_dump", asyncio.shield(task))
        else:
            raw = await step_timing.timed(
                "ui_dump",
                asyncio.wait_for(
                    asyncio.shield(task), max(0.0, deadline - time.monotonic())
                ),
            )
    except asyncio.TimeoutError:
        step_timing.mark("dump_cut")
        return {"error": DUMP_CUT_DETAIL, "content": None}
    if raw.get("error"):
        return raw
    # The display is a DEVICE fact, so it is read from the device rather than
    # inferred from the dump; `display_size` caches it per serial and never
    # refuses, so this costs one round trip per session and cannot fail a dump.
    sized = await adb.display_size(ctx.serial)
    # Cached per serial like the size above, so the accessibility floor is this
    # device's 48dp rather than an assumed one, at no extra round trip.
    dpi = await adb.display_density(ctx.serial)
    observed = composite.observe(
        raw.get("content"),
        await resolve_activity(ctx),
        display=sized.get("content"),
        density=dpi.get("content"),
    )
    _remember(ctx, observed)
    return observed


async def resolve_activity(ctx: object) -> str:
    """THE activity a screen's identity is hashed with. One producer, no other.

    A caller that already knows the activity keeps priority: this only fills the
    gap that made `screen_id` hash two dimensions instead of three. Memoised on
    the context so a replay pays for one probe rather than one per action, and a
    failed probe is remembered as "unknown" rather than retried on every dump.

    PUBLIC, and named without the underscore, because the prune sites OUTSIDE
    this module must ask it rather than answer the question themselves.
    `perception._screen_id` hashes ``package|activity|texts[:3]``, so a site
    that reads the raw ``ctx.activity`` attribute stores a screen under a
    DIFFERENT id from the one this module stamps on the trace -- and the report
    joins exactly those two. Measured: run ``mrun-20260909-202601-5d6cf0``
    stored three screens as ``54746b60347d`` while its trace named
    ``d86961ab9d42``, with identical content hashes proving one dump behind
    both, and shipped a report with three PNGs on disk and no picture under any
    step. ``session.context_for`` builds a fresh context per MCP call with
    ``activity=""``, so a packet-build site never inherits the replay's probe;
    the two answers cannot converge by luck.

    *ctx* is typed ``object`` and read through ``getattr``: `explore_runner`
    takes its context duck-typed, and a producer that only accepts one concrete
    class is a producer half the callers cannot use.
    """
    known = str(getattr(ctx, "activity", "") or "")
    if known:
        return known
    if getattr(ctx, "_activity_probed", False):
        return ""
    serial = _serial_for_activity(ctx)
    probed = await adb.current_activity(serial)
    found = str(probed.get("content") or "")
    try:
        ctx._activity_probed = True
        if found:
            ctx.activity = found
    except (AttributeError, TypeError):
        # A frozen or slotted context must not fail a dump over a cache write.
        return found
    return found


def _serial_for_activity(ctx: object) -> str:
    """The context's serial, warning when it has none."""
    serial = str(getattr(ctx, "serial", "") or "")
    if not serial:
        # Not a refusal -- an empty activity is a NORMAL answer here and a
        # weaker screen identity beats a refused dump. But it is the one way
        # this producer can quietly hand back the two-dimensional id it exists
        # to prevent, so it says so rather than looking like a probe that found
        # nothing on a real device.
        logger.warning(
            "mobile.executor.resolve_activity: no serial on the context; "
            "screen ids will be hashed without an activity"
        )
    return serial


async def _focus_probe(serial: str, budget: float) -> str:
    """The focused ``package/Activity``, or ``""`` when not known in *budget* s.

    One short ``dumpsys window`` line, far cheaper than a dump. Bounded by the
    budget the polled wait has left, and never raises: an unknown focus is
    never a change.
    """
    if budget <= 0:
        return ""
    try:
        probed = await asyncio.wait_for(
            adb.current_activity(serial, timeout=int(budget) + 1), budget
        )
    except (TimeoutError, OSError, ValueError, KeyError):
        return ""
    return str((probed or {}).get("content") or "")
