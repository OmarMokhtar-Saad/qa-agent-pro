"""Replay engine: element labels, tap-target resolution, swipe geometry and miss details. Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

import logging
import re

from tools.mobile import actions as actions_mod
from tools.mobile import (
    perception,
)

logger = logging.getLogger("tools.mobile.executor")


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: object) -> list[str]:
    return _TOKEN_RE.findall(str(text or "").lower())


#: Every string-valued key a pruned element carries. The guard reads ALL of
#: them, and a completeness test asserts this set still matches perception's
#: output -- so adding a field there forces a decision here instead of silently
#: narrowing the evidence. That ratchet is the point: this guard was walked past
#: three times by evidence it did not read.
IDENTIFYING_KEYS: tuple[str, ...] = ("text", "desc", "rid", "cls")


def _split_camel(text: str) -> str:
    """``DeleteAccountButton`` -> ``Delete Account Button``. Delegates.

    The implementation moved to ``perception.split_camel`` when the role
    lexicon needed the same question asked of the same strings: ONE tokenising
    idiom in this package, not three private copies that drift. The name and
    the docstring below are kept because the guard's reasoning lives here.

    Applied to the CLASS name alone. The lexicon matches whole tokens, which is
    what keeps "Deleted items" from being a false positive -- but it also means
    a camel-case run is one token, so ``DeleteAccountButton`` lowercased to
    ``deleteaccountbutton`` never matched ``delete`` and a custom widget class
    walked past the guard. Text and desc are human-written and already
    word-separated, so they are left exactly as the app wrote them.

    Does not widen the lexicon: ``ConfirmationTextView`` becomes
    ``Confirmation Text View`` and ``confirmation`` is still not ``confirm``.
    """
    return perception.split_camel(text)


def _label_part(key: str, value: object) -> str:
    """One identifying field as guard text: cls split, rid raw AND split."""
    text = str(value or "")
    if key == "cls":
        return _split_camel(text)
    if key == "rid":
        # Raw keeps one-token words (CheckOut -> checkout); split adds signOut -> sign out.
        return perception.with_camel_split(text)
    return text


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
    parts = [_label_part(key, element.get(key)) for key in IDENTIFYING_KEYS]
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
    outer = _int_box(element.get("bounds"))
    if outer is None:
        return []
    out: list[str] = []
    for other in screen.get("elements") or []:
        if _is_contained_child(element, other, outer):
            for key in ("text", "desc"):
                value = str(other.get(key) or "")
                if value:
                    out.append(value)
    return out


def _int_box(box: object) -> tuple[int, int, int, int] | None:
    """Four integer bounds, or None when *box* is not a usable rectangle."""
    if not (isinstance(box, (list, tuple)) and len(box) == 4):
        return None
    try:
        x1, y1, x2, y2 = (int(v) for v in box)
    except (TypeError, ValueError, OverflowError):
        return None
    return x1, y1, x2, y2


def _is_contained_child(element: dict, other: object, outer: tuple) -> bool:
    """True when *other* is drawn inside *outer* and is not *element* itself."""
    if not isinstance(other, dict) or other is element:
        return False
    if other.get("id") and other.get("id") == element.get("id"):
        return False
    inner = _int_box(other.get("bounds"))
    if inner is None:
        return False
    x1, y1, x2, y2 = outer
    a1, b1, a2, b2 = inner
    if a1 < x1 or b1 < y1 or a2 > x2 or b2 > y2:
        return False
    # STRICTLY LARGER is skipped; EQUAL is a child.
    #
    # This was `>=`, and the reason it was wrong is that it was doing a job
    # already done: the two checks above exclude an element from being its
    # own child, by identity and by id. What `>=` actually excluded was an
    # ordinary `match_parent` x `match_parent` label inside a clickable
    # wrapper -- the commonest button shape on Android -- so a transfer
    # CTA whose child said "Send money" was judged on
    # 'com.bank.app:id/cta Frame Layout' and tapped through unguarded.
    # Confirmed live by a review that ran it, and unpinned in BOTH
    # directions: flipping the operator left 1697 tests green.
    return (a2 - a1) * (b2 - b1) <= (x2 - x1) * (y2 - y1)


#: The AMBIGUOUS-ROLE message. A formatter rather than a constant because the
#: count is the useful part of it, and ``test_every_miss_message_is_reachable``
#: enumerates it alongside the constants so it cannot become unreachable
#: without saying so.
AMBIGUOUS_ROLE_PREFIX = "That role matched "


def ambiguous_role_detail(count: object) -> str:
    """Their message, kept verbatim: the role was SEEN and not narrowed.

    ``count`` is coerced rather than trusted. It used to be ``str(int(count))``,
    and this module's contract is that it never raises -- a fuzz over 20 inputs
    raised on 18 of them. No caller can reach that today (the only one passes
    ``resolution["candidates"]``, already an int), so this changes no shipped
    message; it removes a way for a future caller to turn a helpful refusal
    into an unhandled exception halfway through a replay.

    An unusable count degrades to the word rather than to a NUMBER: "matched 0
    tappable controls" would be a false statement, and the one thing actually
    known when the count cannot be read is that there was more than one.
    """
    try:
        shown = str(int(count))
    except (TypeError, ValueError, OverflowError):
        # OverflowError is the one this batch's own fuzz caught, in the FIX
        # rather than in the original: `int(float("inf"))` raises it, and it
        # is neither a TypeError nor a ValueError, so a two-exception guard
        # written for a never-raise contract still raised. `nan` goes to
        # ValueError and was already covered, which is exactly why reading
        # the guard was not enough to judge it.
        shown = "several"
    return (
        AMBIGUOUS_ROLE_PREFIX
        + shown
        + " tappable controls on this screen, so the replay stopped rather "
        "than guessing which you meant. Name one of them by its label, its "
        "rid or its short id."
    )


def missing_element_detail(resolved: object) -> str:
    """Why a target resolved to nothing, in the model's own terms.

    Five answers, because they need five different next moves, and a model told
    only "no element matched" re-plans the same way it planned the first time:

    * an AMBIGUOUS role -- the role was SEEN and could not be narrowed, so the
      advice is to name one of them, not to look for something absent;
    * selectors that name DIFFERENT elements -- a stale plan, and picking
      either would be guessing which half to trust;
    * a selector that matched nothing while another matched -- also a stale
      plan, and the remedy names what to switch to;
    * an ``id`` alone that matched nothing -- the commonest miss there is, and
      the one whose remedy (`rid`/text) is worth stating;
    * nothing matched at all -- the planner named something that is not here.

    The ambiguity branch comes FIRST: a role that matched two tappable controls
    has been seen, and saying "nothing matched" about it would be false.
    """
    body = resolved if isinstance(resolved, dict) else {}
    try:
        candidates = int(body.get("candidates") or 0)
    except (TypeError, ValueError, OverflowError):
        candidates = 0
    if candidates > 1:
        return ambiguous_role_detail(candidates)
    if body.get("conflict"):
        return MISS_CONFLICT
    if body.get("stale"):
        # WHICH message depends on whether anything still matched, not on how
        # many selectors were supplied. The cross-selector wording says "one
        # selector matches nothing while another one does" -- true only when
        # something did.
        supplied = tuple(body.get("supplied") or ())
        stale_selectors = tuple(body.get("stale_selectors") or ())
        if len(supplied) > len(stale_selectors):
            return MISS_STALE_SELECTOR
        # Nothing matched. The id-specific remedy ("prefer `rid` or the exact
        # on-screen text") is only USEFUL when the id was the only selector
        # supplied; where a rid or a text missed too, it names what just failed.
        if len(supplied) == 1:
            return MISS_STALE_ID
        return MISS_STALE_ALL
    return MISS_PLAIN


def _center(element: dict) -> tuple[int, int] | None:
    bounds = element.get("bounds") or []
    if len(bounds) != 4:
        return None
    try:
        x1, y1, x2, y2 = (int(v) for v in bounds)
    except (TypeError, ValueError, OverflowError):
        return None
    return (x1 + x2) // 2, (y1 + y2) // 2


_SWIPE = {
    "up": (540, 1500, 540, 500),
    "down": (540, 500, 540, 1500),
    "left": (900, 1000, 200, 1000),
    "right": (200, 1000, 900, 1000),
}

#: How far inside a named element's edge a confined swipe starts and ends, in
#: pixels. Keeps the finger off the border, where a touch belongs to the
#: neighbour as often as to the element.
_SWIPE_INSET = 10

#: The shortest gesture that is still a SWIPE to Android. Below touch slop
#: (8-32 px by density) the framework delivers a click, and a 300 ms press is
#: under the long-press threshold -- so a 2 px "swipe" on a 22 px element was
#: a tap on it, measured by an executing review. Chosen from the platform's
#: slop, not from :data:`_SWIPE_INSET`; the two are unrelated quantities.
_MIN_SWIPE_PX = 24

#: Why a targeted swipe was handed back rather than collapsed into a press.
TOO_SMALL_TO_SWIPE = (
    "That element is too small to swipe on: a swipe confined to its visible "
    "part would not move far enough to be a swipe, and a gesture that does not "
    "move is a tap. Nothing was touched. Scroll without a target, or name the "
    "scrollable list that contains it."
)


def _display_box(display: object) -> tuple | None:
    """*display* as ``(x1, y1, x2, y2)``, or ``None`` when it cannot be used.

    NEVER RAISES and never guesses. A packet with no ``root_bounds``, a
    malformed one, or a degenerate rectangle all return ``None``, which means
    "do not clamp the far edges" -- the pre-existing behaviour. Refusing to
    clamp is the safe direction here: clamping to a wrong or stale rectangle
    would shrink a legitimate gesture out of existence, which is the mistake
    the display work itself had to reverse one round earlier.
    """
    try:
        x1, y1, x2, y2 = (int(v) for v in display)  # type: ignore[misc]
    except (TypeError, ValueError, OverflowError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)


def _panel_far_edges(display: object) -> tuple:
    """The far edges the panel is known to have, as ``(x_far, y_far)``.

    Either may be ``None``, meaning "this axis is unknown, so do not clamp it" --
    the same safe direction :func:`_display_box` already takes for a whole
    missing rectangle, applied one axis at a time. Clamping to a guess is what
    shrinks a legitimate gesture out of existence; clamping to nothing is what
    let a swipe leave the panel. Per axis, neither is forced.

    Accepts BOTH shapes on purpose. Two numbers is the packet's ``panel_axes``,
    where ``0`` marks an axis the panel's own rule could not answer. Four is the
    older ``root_bounds``, which is all-or-nothing by construction, so a packet
    from a build that does not publish ``panel_axes`` clamps exactly as it does
    today. Anything else clamps nothing.

    Never raises: a stored packet is an outside input by the time it is read.
    """
    if isinstance(display, (list, tuple)) and len(display) == 2:
        edges: list = []
        for value in display:
            if isinstance(value, bool):
                # `int(True)` is a 1-pixel panel, which would clamp every
                # gesture to nothing -- the shrinking direction this must never
                # take.
                edges.append(None)
                continue
            try:
                number = int(value)
            except (TypeError, ValueError, OverflowError):
                edges.append(None)
                continue
            edges.append(number if number > 0 else None)
        return (edges[0], edges[1])
    box = _display_box(display)
    if box is None:
        return (None, None)
    return (box[2], box[3])


def _clamp_source(screen: object) -> object:
    """What the clamp reads out of a packet: the per-axis panel, else the rect.

    Both call sites go through here so the two cannot disagree about which key
    wins -- deriving that answer twice in two places is how mirrored conditions
    drift, and this pair of call sites has already been found out of step once.
    """
    body = screen if isinstance(screen, dict) else {}
    axes = body.get("panel_axes")
    if isinstance(axes, (list, tuple)) and len(axes) == 2:
        return axes
    return body.get("root_bounds")


def _on_screen_box(element: dict, display: object) -> tuple[int, int, int, int]:
    """The ON-SCREEN part of *element*'s bounds as ``(x1, y1, x2, y2)``."""
    x1, y1, x2, y2 = (int(v) for v in element["bounds"])
    # The ON-SCREEN part. Near edges always: a negative edge is where
    # `adb._coord` refuses to put the finger. Far edges too WHEN the caller
    # knows the display, because a partly-visible node keeps its far edges and
    # a gesture built from them lands off the panel and silently no-ops.
    # Everything below is computed from this box, so no later clamp can move an
    # endpoint after the invariant has been checked.
    x1, y1 = max(0, x1), max(0, y1)
    frame = _display_box(display)
    if frame is not None:
        # Near edges from the whole rectangle when there is one. A panel's origin
        # is always (0, 0), so this only ever matters for a stored packet that
        # carries something else.
        x1, y1 = max(x1, frame[0]), max(y1, frame[1])
    # FAR edges PER AXIS. An axis the panel's own rule could not answer is left
    # unclamped rather than clamped to a guess -- and before this, ONE
    # unanswerable axis cost the clamp BOTH, so a window laid out above the
    # display produced a swipe to x=2300 against a 1080 panel that the device
    # silently ignored while the step reported success.
    x_far, y_far = _panel_far_edges(display)
    if x_far is not None:
        x2 = min(x2, x_far)
    if y_far is not None:
        y2 = min(y2, y_far)
    return x1, y1, x2, y2


def _centred_swipe(direction: str, points: tuple, box: tuple) -> tuple:
    """The swipe for *direction* centred in *box*, its half-extent the table's
    (*points*) clipped to the box minus :data:`_SWIPE_INSET`."""
    x1, y1, x2, y2 = box
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    if direction in ("up", "down"):
        half = min(
            abs(points[1] - points[3]) // 2, max(0, (y2 - y1) // 2 - _SWIPE_INSET)
        )
        if direction == "up":
            return (cx, cy + half, cx, cy - half)
        return (cx, cy - half, cx, cy + half)
    half = min(abs(points[0] - points[2]) // 2, max(0, (x2 - x1) // 2 - _SWIPE_INSET))
    if direction == "left":
        return (cx + half, cy, cx - half, cy)
    return (cx - half, cy, cx + half, cy)


def _swipe_points(
    direction: str, element: object, display: object = None
) -> tuple | None:
    """The swipe ``_perform`` issues for *direction*, confined to *element*.

    A target-less pan uses the :data:`_SWIPE` table as it is. A TARGETED swipe
    is centred on the element on BOTH axes and clipped to the element's bounds
    minus :data:`_SWIPE_INSET`, so the whole gesture happens on the node the
    action named (shifting by X only once swiped past the named row and across
    controls the guard never judged). A short element gets a short swipe.

    THE INVARIANT, checked LAST on the final tuple: the gesture moves at least
    :data:`_MIN_SWIPE_PX` along its axis, or ``None`` is returned. Checking
    before a clamp once let a mostly off-screen element ship a press.

    The element's ON-SCREEN part (:func:`_on_screen_box`) is FOUR clamps when
    *display* is known, two when not. *display* is the packet's own
    ``root_bounds``, so the guard and the device read one number; absent or
    unusable, the far clamps are SKIPPED rather than guessed. A partly-visible
    row is the NORMAL case: its far edges can lie past the display, and a
    gesture there is silently ignored while the step reports success.

    Returns ``None`` for an unknown direction, and for an element whose
    on-screen part is too small to swipe on; ``_perform`` tells the two apart
    and reports each.
    """
    points = _SWIPE.get(str(direction or ""))
    if not points:
        return None
    if not isinstance(element, dict):
        return points
    center = _center(element)
    if center is None:
        return points
    x1, y1, x2, y2 = _on_screen_box(element, display)
    if x2 <= x1 or y2 <= y1:
        return None
    swipe = _centred_swipe(direction, points, (x1, y1, x2, y2))
    # The post-condition, on the FINAL tuple: a gesture that moves less than
    # touch slop is delivered as a click on the element the script asked to
    # scroll. Handed back instead, so the model names the list.
    if abs(swipe[2] - swipe[0]) + abs(swipe[3] - swipe[1]) < _MIN_SWIPE_PX:
        return None
    return swipe


def _touch_point(
    op: str, action: object, element: object, display: object = None
) -> tuple | None:
    """Where the finger lands FIRST for *op* on *element* -- the point the
    destructive guard judges. Derived from the same helpers ``_perform`` uses,
    so the guard and the device cannot disagree about where the touch is.

    *display* rides along for exactly that reason: it changes where a clamped
    swipe STARTS, so the guard has to judge the clamped point, not the
    unclamped one."""
    if op == "scroll":
        points = _swipe_points(str(getattr(action, "dir", "") or ""), element, display)
        return (points[0], points[1]) if points else None
    if isinstance(element, dict):
        return _center(element)
    return None


def _screen_elements(screen: object) -> list:
    """The element list of a pruned screen, with or without its ``content``
    envelope -- the same two shapes ``actions.candidates_for`` accepts."""
    body = screen if isinstance(screen, dict) else {}
    if isinstance(body.get("content"), dict):
        body = body["content"]
    found = body.get("elements")
    return found if isinstance(found, list) else []


def resolve_tap_text(text: object, screen: object) -> dict:
    """``{"element", "candidates", "ambiguous"}`` for one `tap_text`.

    Counted by the CONTROL a tap would actuate, not by matching nodes. A Button
    carrying "Send" wrapping a TextView carrying "Send" is two nodes and ONE
    control -- both taps land on the same clickable ancestor -- and counting
    nodes refused every ordinary button as ambiguous (probed, before this).
    :func:`actuated_element` is the one producer of "what does a tap here
    actuate", and the same one the destructive guard is judged by, so the count
    and the guard cannot disagree about a screen.
    """
    target, group = actions_mod.tap_text_candidates(text, screen)
    if target is None:
        return {"element": None, "candidates": 0, "ambiguous": False}
    controls = {}
    for element in group:
        point = _center(element)
        actuated = actuated_element(screen, point) if point else None
        chosen = actuated if isinstance(actuated, dict) else element
        controls[actions_mod._identity(chosen)] = chosen
    if len(controls) > actions_mod.TAP_TEXT_MAX_CANDIDATES:
        return {"element": None, "candidates": len(controls), "ambiguous": True}
    resolved = (actions_mod.resolve_target(target, screen) or {}).get("content") or {}
    return {
        "element": resolved.get("element"),
        "candidates": len(controls),
        "ambiguous": False,
    }


def actuated_element(screen: object, point: object) -> dict | None:
    """The element Android delivers a touch at *point* to, as far as a flat
    dump can tell. Never raises.

    A touch goes to the DEEPEST clickable view under the finger: a
    non-clickable child does not consume it, so it bubbles to the nearest
    clickable ancestor, and among nested clickables the innermost wins. A
    pruned screen carries bounds but no tree, so "deepest" is approximated by
    the SMALLEST clickable element whose bounds contain the point. Z-order is
    not in the dump either; overlapping siblings are resolved by area, and an
    EQUAL-area tie goes to the LATER element, because uiautomator lists
    siblings in draw order and Android hit-tests the top-most first. A larger
    sheet drawn over a smaller chip still loses to the chip -- a stated limit,
    not a claim.

    ``None`` when nothing clickable is under the point -- an EditText in a
    plain layout, say -- in which case the resolved element is all there is to
    judge.
    """
    if not isinstance(screen, dict):
        return None
    if not (isinstance(point, (list, tuple)) and len(point) == 2):
        return None
    try:
        x, y = int(point[0]), int(point[1])
    except (TypeError, ValueError, OverflowError):
        return None
    best: dict | None = None
    best_area = 0
    for other in screen.get("elements") or []:
        if not isinstance(other, dict) or not other.get("clickable"):
            continue
        box = other.get("bounds")
        if not (isinstance(box, (list, tuple)) and len(box) == 4):
            continue
        try:
            x1, y1, x2, y2 = (int(v) for v in box)
        except (TypeError, ValueError, OverflowError):
            continue
        if x < x1 or x > x2 or y < y1 or y > y2:
            continue
        area = max(0, x2 - x1) * max(0, y2 - y1)
        if best is None or area <= best_area:
            best, best_area = other, area
    return best


#: Ops that really touch the device. ``actions.MUTATING_OPS`` minus ``wait``:
#: a wait forces a re-dump (which is why it is a mutating op there) but it
#: actuates nothing, and counting it as progress charged an escape for a stop
#: that had moved nothing. Derived from that frozenset rather than restated, so
#: an op added there is covered here by construction.
ACTUATING_OPS: frozenset[str] = (
    frozenset(actions_mod.MUTATING_OPS) - actions_mod.WAIT_OPS
)

#: Why a target did not resolve, in the words the model reads on its next turn.
#: Constants so a test asserts this module's own text instead of a copy of it,
#: and so the three cases stay distinguishable in a trace a tester reads.
MISS_PLAIN = (
    "No element on this screen matched that target, so the replay stopped here "
    "rather than tapping something else."
)
#: The LONE-selector case, and the one a model meets most often: a target
#: carrying only an `id`, after a `type` moved every element on the screen.
#: It keeps the REMEDY, which is the whole value of the message -- a generic
#: "a selector did not resolve" leaves the model to re-plan by `id` again and
#: burn both free stops and then real escapes.
MISS_STALE_ID = (
    "The `id` in that target is not on this screen. An id describes an element "
    "as it was on the screen you planned from, and this screen has changed, so "
    "nothing was tapped. Re-plan from the screen below, and prefer `rid` or the "
    "exact on-screen text -- those survive a re-layout, an id does not."
)

#: EVERY selector missed, and more than one was supplied. Its remedy cannot be
#: "prefer `rid` or the text" -- those are what just failed. Measured: three of
#: the four shapes that used to reach MISS_STALE_ID supplied a rid and/or a text
#: that also matched nothing, so the message recommended the selectors the model
#: had just tried, and the model re-planned the same way and spent its free
#: stops. Nothing it said was false, which is why only a reachability predicate
#: tight enough to separate the shapes could catch it.
MISS_STALE_ALL = (
    "None of that target's selectors matches anything on this screen, so the "
    "plan was built on a screen that has since changed and nothing was tapped. "
    "Do not retry the same selectors. Re-plan from the screen below: take the "
    "`rid` or the exact text of the control you want from THIS element list."
)

#: The CROSS-SELECTOR case: two or more selectors were supplied and at least one
#: of them resolved. Only reachable then, which
#: ``test_every_miss_message_is_reachable_and_accurate`` asserts against the
#: 63-target enumeration -- because this wording shipped on the lone-id path,
#: telling a model that two selectors disagreed when it had sent one.
MISS_STALE_SELECTOR = (
    "One selector in that target matches nothing on this screen while another "
    "one does, so the plan was built on a screen that has since changed and "
    "nothing was tapped. Re-plan from the screen below. If the stale one was "
    "the `id`, prefer `rid` or the exact on-screen text -- those survive a "
    "re-layout, an id does not."
)
MISS_CONFLICT = (
    "That target's selectors point at DIFFERENT elements on this screen, which "
    "means the plan was made against an older one. Nothing was tapped and none "
    "of them was chosen. Re-plan from the screen below."
)
