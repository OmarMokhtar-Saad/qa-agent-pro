"""Per-call state of the mobile step handlers: ContextVars, sinks and their accessors."""

from __future__ import annotations

from contextvars import ContextVar

#: THIS CALL's captured screens, as ``_image_content_blocks`` specs.
#:
#: A ContextVar rather than a module list, and that is not a style choice: two
#: chats can drive two runs through this process at once (which is why
#: ``mcp_server._inflight_enter`` exists), and a module-level accumulator would
#: attach one run's screen to the other run's reply. Each ``*_content`` entry
#: point below sets its OWN empty list, so a spec can only ever be read by the
#: call that produced it.
#:
#: ``None`` -- the default -- is a THIRD state and it is meaningful: it says
#: "this caller cannot carry image content", which is true of every caller of the
#: ``str``-returning names. ``_mobile_packet_text`` reads that as "do not even
#: ask the device", so the ~100 existing callers of those names pay no extra adb
#: round trip and see no behaviour change at all.
#:
#: WHY A SINK AT ALL, rather than returning tuples: the packet text is produced
#: deep inside ``_mobile_next`` and ``handle_submit_mobile_step``, behind roughly
#: forty ``return`` statements between them -- the lease check, the takeover
#: path, the device-lock refusal, the kill-switch refusal, the never-raise exit.
#: Threading a tuple back would have rewritten every one of those, for a value
#: only the outermost frame uses. ONE producer writes here and exactly two
#: readers drain it.
_MOBILE_IMAGE_SPECS: ContextVar = ContextVar("_MOBILE_IMAGE_SPECS", default=None)

#: The run-level ``env=`` the tester declared on THIS ``qa_mobile_test`` call,
#: parsed and validated. ``_mobile_hand_off`` stores it once in the manifest;
#: a ContextVar because the value has to cross frames that do not carry it.
_MOBILE_RUN_ENV: ContextVar = ContextVar("_MOBILE_RUN_ENV", default=None)

#: Strong references to in-flight learn tasks (the loop only keeps weak ones).
_MOBILE_LEARN_TASKS: set = set()


#: Armed by ``handle_submit_mobile_step_content`` and marked by
#: ``handle_submit_mobile_step`` once the call HOLDS the run (claim, then the
#: device lock). The wrapper saves the call's ``note`` only when it was marked,
#: so a refused call (another chat's lease, a takeover, a busy device, a bad
#: run id) leaves no note behind. A ContextVar for the reason given above.
_MOBILE_STEP_HELD: ContextVar = ContextVar("_MOBILE_STEP_HELD", default=None)


def _note_mobile_step_case(case: object) -> None:
    """Hand the step's case to the wrapper that called it (a saved flow reads its trace).
    A no-op outside the wrapper."""
    held = _MOBILE_STEP_HELD.get()
    if isinstance(held, dict) and isinstance(case, dict):
        held["case"] = case


def _mark_mobile_step_held() -> None:
    """Record that this submit call holds its run. A no-op outside the wrapper."""
    held = _MOBILE_STEP_HELD.get()
    if isinstance(held, dict):
        held["held"] = True


def _mobile_route_kwargs() -> dict:
    """``{'route_expect': [...]}`` when the wrapper is replaying a saved route, else ``{}``.
    Conditional on purpose: a call that is not a route replay passes session.submit nothing new."""
    held = _MOBILE_STEP_HELD.get()
    expect = held.get("route_expect") if isinstance(held, dict) else None
    return {"route_expect": list(expect)} if expect else {}


#: What THIS call wants from the screenshot (S1, dump-first): ``{"asked": bool}``
#: from the tool's ``screenshot`` argument, plus ``"visual": True`` once the
#: submit path sees a ``visual`` assert stop. A dict, and mutated in place,
#: because the submit path and the packet renderer run in the same context.
#: ``None`` (every ``str``-returning caller) reads as "not asked".
_MOBILE_SHOT_WANT: ContextVar = ContextVar("_MOBILE_SHOT_WANT", default=None)

#: What replaces ``screen_block`` when the S8 memo says this chat already holds
#: the byte-identical list. The fingerprint lets a reader match the two.
_MOBILE_SAME_SCREEN_NOTE = (
    "SAME SCREEN as the last packet this chat was sent (fingerprint {fp}): the "
    "element list you already have is still exact, so it is not repeated. "
    "Plan from it."
)


_MOBILE_TOOL_FAILURES = (
    OSError,
    RuntimeError,
    TimeoutError,
    ValueError,
    LookupError,
    TypeError,
    AttributeError,
    ImportError,
)
