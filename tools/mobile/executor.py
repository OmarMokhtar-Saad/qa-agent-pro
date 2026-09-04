"""Replay one validated script against a device, and stop honestly.

Four exits, and which one you get is the whole design:

* ``done``          -- the script ran out or reached ``done``.
* ``needs_model``   -- a target was not on the screen, or an assert failed. The
  trace and the NEW screen go back to the tester's model, which continues the
  script from there. This is the escape hatch, not a failure.
* ``needs_tester``  -- an ``ask_tester`` action, or the destructive guard stopped
  in front of a confirm/delete/pay control.
* ``error``         -- the DEVICE failed (adb gone, dump unparseable). The one
  case where nothing useful can be asked of anybody.

Secrets: a ``type`` action carrying ``secret: true`` is replayed through
``ime.type_text``, whose payload rides on stdin and never becomes an argv
element (Phase 1 proved the transport). This module owes the other half -- the
RECORD. Every trace entry stores ``actions.redact_action(...)``, so the value is
``***`` in the returned trace, in the checkpoint written from it, and in
anything a handler builds out of it.

The destructive guard matches WHOLE WORDS, not substrings. "Confirm" stops;
"Deleted items" does not, because its token is ``deleted`` and the lexicon holds
``delete``. A guard that stops everything is exactly as broken as one that stops
nothing, which is why both directions are pinned.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import time

from tools.mobile import actions as actions_mod
from tools.mobile import adb, ime, perception

logger = logging.getLogger(__name__)

# Whole-word terms that mean "this tap is not undoable". English plus the Latin
# script variants a tester is likely to meet on a localised build.
#
# NO Arabic (or other non-Latin) literals appear here on purpose: the programme's
# release gate greps this diff for Arabic literals to prove nothing leaked from
# the private reference project, and a legitimate lexicon entry would be
# indistinguishable from a leak. Adding non-Latin variants is a follow-up that
# must land with its own provenance note, NOT a drive-by.
DESTRUCTIVE_LEXICON = (
    "confirm",
    "confirmar",
    "confirmer",
    "delete",
    "eliminar",
    "supprimer",
    "remove",
    "erase",
    "wipe",
    "uninstall",
    "pay",
    "pagar",
    "payer",
    "purchase",
    "checkout",
    "buy",
    "submit",
    "send",
    "transfer",
    "withdraw",
    "reset",
    "deactivate",
    "close account",
    "sign out",
    "log out",
    "logout",
    "yes, delete",
)

GUARD_DETAIL = (
    "Stopped before a control that looks irreversible. Nothing was tapped. "
    "Confirm with the tester, then resubmit the script with the same action."
)

MAX_TRACE = 80
_TOKEN_RE = re.compile(r"[a-z0-9]+")

STATUS_DONE = "done"
STATUS_NEEDS_MODEL = "needs_model"
STATUS_NEEDS_TESTER = "needs_tester"
STATUS_ERROR = "error"

#: A "done" carrying verdict=pass that recorded no assert_pass and no screen
#: change is not evidence of anything -- see ``_has_verification``.
STATUS_UNVERIFIED = "unverified"

#: How often ``wait`` with ``until_text`` re-dumps the screen while polling.
WAIT_POLL_S = 1.0

#: The bound for ``wait`` with ``until_text`` and no explicit ``ms`` -- keeps a
#: script that forgot to set ms from polling forever.
DEFAULT_WAIT_UNTIL_TEXT_S = 20.0


@dataclasses.dataclass
class Context:
    """Everything a replay needs about the device it is driving."""

    serial: str
    package: str = ""
    activity: str = ""
    guard_destructive: bool = True
    screen: dict | None = None
    # One tester-supplied value per field, held for THIS call only and never
    # written anywhere. ``case_runner`` fills it from the submit argument.
    tester_inputs: dict | None = None


#: The ONLY ops the destructive guard skips, because none can actuate anything:
#: an assert reads the screen, and ``done`` / ``ask_tester`` end the replay
#: before any device call. Everything else is guarded whether or not today's
#: implementation happens to touch the screen -- an allow-list of "ops that
#: tap" was wrong twice (``clear``, then a targeted ``scroll``), because the
#: guard is about the EFFECT and not the primitive.
#:
#: Adding an op here is a security decision and needs a stated reason. Pinned
#: behaviourally by the parametrised guard test in tests/mobile, which drives
#: every op in the vocabulary against a destructive control and fails on an op
#: it has never seen.
NON_ACTUATING_OPS: frozenset[str] = frozenset({"assert", "done", "ask_tester"})


def _tokens(text: object) -> list[str]:
    return _TOKEN_RE.findall(str(text or "").lower())


#: Every string-valued key a pruned element carries. The guard reads ALL of
#: them, and a completeness test asserts this set still matches perception's
#: output -- so adding a field there forces a decision here instead of silently
#: narrowing the evidence. That ratchet is the point: this guard was walked past
#: three times by evidence it did not read.
IDENTIFYING_KEYS: tuple[str, ...] = ("text", "desc", "rid", "cls")


def _split_camel(text: str) -> str:
    """``DeleteAccountButton`` -> ``Delete Account Button``.

    Applied to the CLASS name alone. The lexicon matches whole tokens, which is
    what keeps "Deleted items" from being a false positive -- but it also means
    a camel-case run is one token, so ``DeleteAccountButton`` lowercased to
    ``deleteaccountbutton`` never matched ``delete`` and a custom widget class
    walked past the guard. Text and desc are human-written and already
    word-separated, so they are left exactly as the app wrote them.

    Does not widen the lexicon: ``ConfirmationTextView`` becomes
    ``Confirmation Text View`` and ``confirmation`` is still not ``confirm``.
    """
    if not text:
        return ""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", spaced)


def element_label(element: object, screen: object = None) -> str:
    """Everything about *element* a destructive lexicon should judge.

    The guard read ``text`` and ``desc`` only, and four routes walked past it,
    each confirmed by execution:

    * the word only in the RESOURCE-ID -- a button showing "Continue" whose id
      is ``id/delete_account``. For an icon-only control the rid is usually the
      ONLY evidence there is, which is why dropping it mattered most.
    * the word only in the CLASS name, e.g. a custom ``DeleteAccountButton``.
    * the word only in a CHILD node: a clickable row whose own text is empty,
      wrapping a label reading "Delete account". That is the ordinary Android
      list-item shape, not an exotic one.
    * the same element selected by ``id`` rather than ``rid``, which changed
      the verdict for one control, because the ACTION's text contributed the rid
      and the element's label did not.

    Containment is consulted ONLY for an element with no label of its own. A
    labelled button is judged on its label; a bare wrapper is judged on what it
    visibly contains. Reading descendants unconditionally would let a
    screen-sized container inherit every word on screen and stop ordinary taps.

    THE RESIDUAL, stated rather than implied: a control carrying no identifying
    string at all -- empty text, empty desc, no rid, a generic class -- cannot
    be classified by any lexicon, and this does not change that. The guard is a
    strong default, never a proof.
    """
    if not isinstance(element, dict):
        return ""
    parts = [
        _split_camel(str(element.get(key) or ""))
        if key == "cls"
        else str(element.get(key) or "")
        for key in IDENTIFYING_KEYS
    ]
    if element.get("text") or element.get("desc"):
        return " ".join(p for p in parts if p).strip()
    parts.extend(_contained_text(element, screen))
    return " ".join(p for p in parts if p).strip()


def _contained_text(element: dict, screen: object) -> list[str]:
    """Text of the elements drawn INSIDE *element*.

    Only reached for an element with no text or desc of its own. Bounds are the
    only containment signal a pruned screen carries -- the tree shape is gone by
    design -- so "inside" means strictly smaller and fully within, and an
    element is never its own child.
    """
    if not isinstance(screen, dict):
        return []
    box = element.get("bounds")
    if not (isinstance(box, (list, tuple)) and len(box) == 4):
        return []
    try:
        x1, y1, x2, y2 = (int(v) for v in box)
    except (TypeError, ValueError):
        return []
    own_area = (x2 - x1) * (y2 - y1)
    out: list[str] = []
    for other in screen.get("elements") or []:
        if not isinstance(other, dict) or other is element:
            continue
        if other.get("id") and other.get("id") == element.get("id"):
            continue
        inner = other.get("bounds")
        if not (isinstance(inner, (list, tuple)) and len(inner) == 4):
            continue
        try:
            a1, b1, a2, b2 = (int(v) for v in inner)
        except (TypeError, ValueError):
            continue
        if a1 < x1 or b1 < y1 or a2 > x2 or b2 > y2:
            continue
        if (a2 - a1) * (b2 - b1) >= own_area:
            continue
        for key in ("text", "desc"):
            value = str(other.get(key) or "")
            if value:
                out.append(value)
    return out


def destructive_hit(text: object) -> str:
    """The lexicon term *text* matches as a whole word, or ``""``.

    Word-boundary matching is the entire point. ``"Deleted items"`` tokenises to
    ``["deleted", "items"]`` and matches nothing; ``"Confirm payment"`` matches
    ``confirm``. A multi-word entry is matched as a contiguous token run.
    """
    tokens = _tokens(text)
    if not tokens:
        return ""
    for term in DESTRUCTIVE_LEXICON:
        wanted = _tokens(term)
        if not wanted:
            continue
        span = len(wanted)
        for start in range(0, len(tokens) - span + 1):
            if tokens[start : start + span] == wanted:
                return term
    return ""


def is_destructive(text: object) -> bool:
    return bool(destructive_hit(text))


async def _dump(ctx: Context) -> dict:
    """Re-dump and re-prune. Returns the ``{"error","content"}`` shape."""
    raw = await adb.uiautomator_dump(ctx.serial)
    if raw.get("error"):
        return raw
    return perception.prune(raw.get("content"), ctx.activity)


#: The wall clock, bound at import. Tests replace `executor.time` with a fake
#: that carries only `monotonic` to drive the duration stamp; the absolute `at`
#: below must keep reading the REAL clock under that fake, and must stay the
#: same clock `run_store` stamps `started`/`updated` with.
_wall_clock = time.time


def _entry(index: int, action: object, before: str, started: float) -> dict:
    return {
        "index": int(index),
        "action": actions_mod.redact_action(action),
        "before_screen_id": str(before or ""),
        "after_screen_id": "",
        # Stamped by ``_append``, AFTER the action ran. Computing it here read
        # the clock at creation, so every real run carried ``ms: 0`` -- a
        # 4000 ms wait included -- and the report could time nothing.
        "ms": 0,
        "_started": float(started),
        # Host wall clock at the moment the action BEGAN, the same clock the
        # case checkpoint's `started`/`updated` use, so app-log evidence can be
        # placed between actions by absolute time (plan mobile-app-evidence D6).
        "at": _wall_clock(),
        "outcome": "",
        "detail": "",
    }


def _screen_id(screen: object) -> str:
    return (
        str((screen or {}).get("screen_id") or "") if isinstance(screen, dict) else ""
    )


def _screen_has(screen: object, needle: str) -> bool:
    want = " ".join(str(needle or "").split()).lower()
    if not want or not isinstance(screen, dict):
        return False
    for element in screen.get("elements") or []:
        if not isinstance(element, dict):
            continue
        for field in ("text", "desc"):
            if want in " ".join(str(element.get(field) or "").split()).lower():
                return True
    return False


def _has_verification(trace: list[dict]) -> bool:
    """True when *trace* holds evidence a ``done verdict=pass`` can stand on.

    Either an explicit ``assert_pass``, or a mutating op whose screen changed
    (``before_screen_id != after_screen_id`` on an ``ok`` entry) counts. A
    trace with neither is not proof of anything, however confident the
    model's own ``reason`` text sounds.
    """
    for item in trace:
        outcome = str(item.get("outcome") or "")
        if outcome == "assert_pass":
            return True
        if outcome == "ok":
            before = str(item.get("before_screen_id") or "")
            after = str(item.get("after_screen_id") or "")
            if before and after and before != after:
                return True
    return False


async def _wait_until_text(
    ctx: Context, screen: object, text: str, ms: int
) -> tuple[bool, object, bool]:
    """Poll the screen until *text* appears or the budget runs out.

    Returns ``(found, latest_screen, timed_out)``. ``ms`` is the caller's cap;
    ``0`` falls back to :data:`DEFAULT_WAIT_UNTIL_TEXT_S` so a script that
    forgot to set it does not poll forever.
    """
    budget = (ms / 1000.0) if ms else DEFAULT_WAIT_UNTIL_TEXT_S
    deadline = time.monotonic() + budget
    current = screen
    while True:
        if _screen_has(current, text):
            return True, current, False
        if time.monotonic() >= deadline:
            return False, current, True
        await _sleep(WAIT_POLL_S)
        dumped = await _dump(ctx)
        if dumped.get("error"):
            return False, current, True
        current = dumped.get("content")


def _center(element: dict) -> tuple[int, int] | None:
    bounds = element.get("bounds") or []
    if len(bounds) != 4:
        return None
    try:
        x1, y1, x2, y2 = (int(v) for v in bounds)
    except (TypeError, ValueError):
        return None
    return (x1 + x2) // 2, (y1 + y2) // 2


_SWIPE = {
    "up": (540, 1500, 540, 500),
    "down": (540, 500, 540, 1500),
    "left": (900, 1000, 200, 1000),
    "right": (200, 1000, 900, 1000),
}


async def replay(script: object, ctx: Context) -> dict:
    """Execute *script* against ``ctx``. Never raises.

    ``{"error": None, "content": {"status", "trace", "screen", "verdict",
    "reason", "stopped_at"}}``. Top-level ``error`` is reserved for this
    function itself failing -- a device refusal is ``status == "error"`` with a
    trace, because the trace is the only thing that tells a tester how far the
    case got.
    """
    trace: list[dict] = []
    screen = ctx.screen if isinstance(ctx.screen, dict) else None
    try:
        items = list(getattr(script, "actions", None) or [])
        if not items:
            return {
                "error": None,
                "content": _result(
                    STATUS_ERROR, trace, screen, "", "The script had no actions.", -1
                ),
            }
        if screen is None:
            dumped = await _dump(ctx)
            if dumped.get("error"):
                return {
                    "error": None,
                    "content": _result(
                        STATUS_ERROR, trace, None, "", str(dumped["error"]), -1
                    ),
                }
            screen = dumped.get("content")

        for index, action in enumerate(items):
            started = time.monotonic()
            entry = _entry(index, action, _screen_id(screen), started)
            op = str(getattr(action, "op", "") or "")

            # --- terminal ops -------------------------------------------------
            if op == "done":
                verdict = str(getattr(action, "verdict", ""))
                reason = str(getattr(action, "reason", ""))[:400]
                if verdict == "pass" and not _has_verification(trace):
                    verdict = STATUS_UNVERIFIED
                    reason = (
                        "verdict=pass was not accepted: this case's trace has "
                        "no assert_pass and no screen change, so nothing was "
                        "verified. " + reason
                    ).strip()
                entry["outcome"] = "done"
                entry["detail"] = reason
                entry["after_screen_id"] = _screen_id(screen)
                _append(trace, entry)
                return {
                    "error": None,
                    "content": _result(
                        STATUS_DONE,
                        trace,
                        screen,
                        verdict,
                        reason,
                        index,
                    ),
                }
            if op == "ask_tester":
                field = str(getattr(action, "field", ""))
                supplied = (ctx.tester_inputs or {}).get(field)
                if supplied is None:
                    entry["outcome"] = "needs_tester"
                    entry["detail"] = str(getattr(action, "prompt", ""))[:300]
                    entry["after_screen_id"] = _screen_id(screen)
                    _append(trace, entry)
                    return {
                        "error": None,
                        "content": _result(
                            STATUS_NEEDS_TESTER,
                            trace,
                            screen,
                            "",
                            entry["detail"],
                            index,
                            field=field,
                        ),
                    }
                entry["outcome"] = "supplied"
                entry["detail"] = "the tester supplied " + field
                entry["after_screen_id"] = _screen_id(screen)
                _append(trace, entry)
                continue

            # --- target resolution -------------------------------------------
            target = getattr(action, "target", None)
            element = None
            if target is not None:
                resolved = actions_mod.resolve_target(target, screen)
                element = ((resolved or {}).get("content") or {}).get("element")
                # ``assert`` is exempt for the same reason ``scroll`` is, and it
                # matters more: a missing element IS the answer an
                # ``assert kind="element"`` was asked for. Letting the generic
                # boomerang intercept it meant that assert could never FAIL --
                # the one verdict it exists to produce. It returned
                # ``needs_model`` instead, so a tester who required an element
                # that was absent got a re-plan, three spent escapes and a
                # ``blocked`` case rather than the failure they asked for.
                # ``_evaluate_assert`` decides presence itself.
                if element is None and op not in ("scroll", "assert"):
                    entry["outcome"] = "missing_element"
                    entry["detail"] = (
                        "No element on this screen matched that target, so the "
                        "replay stopped here rather than tapping something else."
                    )
                    entry["after_screen_id"] = _screen_id(screen)
                    _append(trace, entry)
                    return {
                        "error": None,
                        "content": _result(
                            STATUS_NEEDS_MODEL,
                            trace,
                            screen,
                            "",
                            entry["detail"],
                            index,
                        ),
                    }

            # --- destructive guard -------------------------------------------
            # DENY BY DEFAULT. This was an allow-list of ops believed to tap,
            # and it was wrong twice: 'clear' taps to focus the field, and a
            # TARGETED 'scroll' re-centres its swipe on the resolved element,
            # so "Slide to confirm payment" was actuated while the same control
            # under 'tap' was stopped. The guard exists to stop an IRREVERSIBLE
            # ACTION; which primitive delivers it is incidental, and an
            # allow-list must be remembered every time one is added.
            #
            # So every op is guarded unless it is declared inert. A target-less
            # pan still passes: it resolves no element and there is no
            # destructive text in "scroll down".
            if ctx.guard_destructive and op not in NON_ACTUATING_OPS:
                label = " ".join(
                    [
                        actions_mod.action_text(action),
                        element_label(element, screen),
                    ]
                ).strip()
                hit = destructive_hit(label)
                if hit:
                    entry["outcome"] = "guard_stop"
                    entry["detail"] = GUARD_DETAIL + " Matched: " + hit
                    entry["after_screen_id"] = _screen_id(screen)
                    _append(trace, entry)
                    return {
                        "error": None,
                        "content": _result(
                            STATUS_NEEDS_TESTER,
                            trace,
                            screen,
                            "",
                            entry["detail"],
                            index,
                            guard_term=hit,
                        ),
                    }

            # --- asserts ------------------------------------------------------
            if op == "assert":
                ok, detail = _evaluate_assert(action, screen, trace)
                entry["outcome"] = "assert_pass" if ok else "assert_fail"
                entry["detail"] = detail
                entry["after_screen_id"] = _screen_id(screen)
                _append(trace, entry)
                if ok:
                    continue
                return {
                    "error": None,
                    "content": _result(
                        STATUS_NEEDS_MODEL, trace, screen, "", detail, index
                    ),
                }

            # --- wait / until_text ---------------------------------------------
            if op == "wait":
                until_text = str(getattr(action, "until_text", "") or "").strip()
                ms = int(getattr(action, "ms", 0) or 0)
                if until_text:
                    found, screen, _timed_out = await _wait_until_text(
                        ctx, screen, until_text, ms
                    )
                    entry["outcome"] = "ok" if found else "wait_timeout"
                    entry["detail"] = (
                        "found " + repr(until_text[:120])
                        if found
                        else "timed out waiting for " + repr(until_text[:120])
                    )
                    entry["after_screen_id"] = _screen_id(screen)
                    _append(trace, entry)
                    if found:
                        continue
                    return {
                        "error": None,
                        "content": _result(
                            STATUS_NEEDS_MODEL,
                            trace,
                            screen,
                            "",
                            entry["detail"],
                            index,
                        ),
                    }
                if ms:
                    await _sleep(ms / 1000.0)
                entry["outcome"] = "ok"
                entry["detail"] = "waited " + str(ms) + "ms"
                entry["after_screen_id"] = _screen_id(screen)
                _append(trace, entry)
                continue

            # --- device ops ---------------------------------------------------
            outcome = await _perform(op, action, element, ctx)
            if outcome.get("error"):
                entry["outcome"] = "device_error"
                entry["detail"] = str(outcome["error"])[:400]
                entry["after_screen_id"] = _screen_id(screen)
                _append(trace, entry)
                return {
                    "error": None,
                    "content": _result(
                        STATUS_ERROR, trace, screen, "", entry["detail"], index
                    ),
                }
            entry["outcome"] = "ok"
            entry["detail"] = str((outcome.get("content") or {}).get("detail") or "")

            if op in actions_mod.MUTATING_OPS:
                before_id = str(entry.get("before_screen_id") or "")
                dumped = await _dump(ctx)
                if dumped.get("error"):
                    entry["outcome"] = "dump_failed"
                    entry["detail"] = str(dumped["error"])[:400]
                    _append(trace, entry)
                    return {
                        "error": None,
                        "content": _result(
                            STATUS_ERROR, trace, screen, "", entry["detail"], index
                        ),
                    }
                screen = dumped.get("content")
                if _screen_id(screen) == before_id:
                    entry["outcome"] = "no_change"
            entry["after_screen_id"] = _screen_id(screen)
            _append(trace, entry)

        # A script that never calls done() lands here. It used to hand back an
        # empty verdict, which `case_runner` reads as PASS -- so omitting one
        # word bypassed the whole verification rule the done() branch enforces.
        # The same rule applies on both exits or it is not a rule.
        ended_verified = _has_verification(trace)
        return {
            "error": None,
            "content": _result(
                STATUS_DONE,
                trace,
                screen,
                "" if ended_verified else STATUS_UNVERIFIED,
                "The script finished without a done() action."
                + (
                    ""
                    if ended_verified
                    else " Nothing in it asserted anything or changed the screen,"
                    " so there is no evidence this case passed."
                ),
                len(items) - 1,
            ),
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.executor.replay failed")
        return {"error": str(exc), "content": None}


def _append(trace: list[dict], entry: dict) -> None:
    """Stamp the action's duration and keep the entry. The private ``_started``
    mark never reaches the trace: it is popped here, on every path."""
    started = entry.pop("_started", None)
    if started is not None:
        entry["ms"] = int(max(0.0, time.monotonic() - float(started)) * 1000)
    if len(trace) < MAX_TRACE:
        trace.append(entry)


def _result(
    status: str,
    trace: list[dict],
    screen: object,
    verdict: str,
    reason: str,
    stopped_at: int,
    **extra: object,
) -> dict:
    payload = {
        "status": str(status),
        "trace": list(trace),
        "screen": screen if isinstance(screen, dict) else None,
        "verdict": str(verdict or ""),
        "reason": str(reason or ""),
        "stopped_at": int(stopped_at),
    }
    payload.update(extra)
    return payload


def _evaluate_assert(
    action: object, screen: object, trace: list[dict]
) -> tuple[bool, str]:
    kind = str(getattr(action, "kind", "") or "")
    text = str(getattr(action, "text", "") or "")
    if kind == "text_present":
        found = _screen_has(screen, text)
        return found, ("found " if found else "NOT found on this screen: ") + repr(
            text[:120]
        )
    if kind == "text_absent":
        found = _screen_has(screen, text)
        return (not found), (
            "still on this screen: " if found else "absent, as expected: "
        ) + repr(text[:120])
    if kind == "element":
        resolved = actions_mod.resolve_target(getattr(action, "target", None), screen)
        element = ((resolved or {}).get("content") or {}).get("element")
        return bool(element), (
            "the element is on this screen"
            if element
            else "the element is not on this screen"
        )
    # screen_changed
    seen = [
        entry.get("before_screen_id")
        for entry in trace
        if entry.get("before_screen_id")
    ]
    current = _screen_id(screen)
    if not seen:
        return False, "no earlier screen to compare against"
    changed = current != seen[0]
    return changed, (
        "the screen changed since the first action"
        if changed
        else "the screen did not change since the first action"
    )


async def _perform(op: str, action: object, element: object, ctx: Context) -> dict:
    """One device op. Every argv is built by ``adb``, which validates again."""
    serial = ctx.serial
    if op == "back":
        return await adb.keyevent(serial, "KEYCODE_BACK")
    if op == "home":
        return await adb.keyevent(serial, "KEYCODE_HOME")
    if op == "launch":
        if not ctx.package:
            return {"error": "No app package is set for this run.", "content": None}
        return await adb.launch(serial, ctx.package)
    if op == "open_url":
        return await adb.open_url(serial, str(getattr(action, "url", "")))
    if op == "wait":
        ms = int(getattr(action, "ms", 0) or 0)
        if ms:
            await _sleep(ms / 1000.0)
        return {"error": None, "content": {"detail": "waited " + str(ms) + "ms"}}
    if op == "scroll":
        points = _SWIPE.get(str(getattr(action, "dir", "") or ""))
        if not points:
            return {"error": "Unknown scroll direction.", "content": None}
        if isinstance(element, dict):
            center = _center(element)
            if center:
                dx = center[0] - 540
                points = (
                    points[0] + dx,
                    points[1],
                    points[2] + dx,
                    points[3],
                )
        return await adb.swipe(serial, *points)
    if op == "tap":
        center = _center(element) if isinstance(element, dict) else None
        if not center:
            return {"error": "That element has no usable bounds.", "content": None}
        return await adb.tap(serial, center[0], center[1])
    if op in ("type", "clear"):
        center = _center(element) if isinstance(element, dict) else None
        if not center:
            return {"error": "That field has no usable bounds.", "content": None}
        focused = await adb.tap(serial, center[0], center[1])
        if focused.get("error"):
            return focused
        if op == "clear":
            return await ime.clear(serial)
        secret = bool(getattr(action, "secret", False))
        if secret:
            field = str(getattr(action, "field", "") or "")
            value = (ctx.tester_inputs or {}).get(field)
            if value is None:
                return {
                    "error": (
                        "No tester-supplied value is held for the field "
                        + repr(field[:60])
                        + ", so nothing was typed. Ask for it first with "
                        "ask_tester."
                    ),
                    "content": None,
                }
            return await ime.type_text(serial, str(value), secret=True)
        return await ime.type_text(
            serial, str(getattr(action, "text", "") or ""), secret=False
        )
    return {"error": "Unsupported action " + repr(op), "content": None}


async def _sleep(seconds: float) -> None:
    """Named so a test can patch it; a real sleep would be the slowest thing here."""
    import asyncio

    await asyncio.sleep(seconds)
