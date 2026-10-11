"""Phone frames, wireframes and screenshot rendering for the report, including the screen look resolution."""

from __future__ import annotations

import logging
from contextvars import ContextVar

from tools.mobile import report_images, run_store, screen_phone
from tools.mobile.report_media import _IMAGES, _img_html
from tools.mobile.report_text import _text, esc
from tools.mobile.report_widgets import _PageData

# same logger as the old module; kept for log filters
logger = logging.getLogger("tools.mobile.report")


#: The CEILING the rendered wireframe box may not exceed, in CSS px. REVERSED
#: from a fixed 360x800 box (docs/DECISIONS.md -> "The frame takes the
#: device's own aspect"): a fixed frame let a landscape phone draw at a fifth
#: of the available area inside a tall empty box. `_frame_box` now fits the
#: device's own aspect INSIDE this ceiling, so a full-screen element always
#: fills its frame.
MAX_FRAME_W = 360


MAX_FRAME_H = 800

#: ``perception.MAX_ELEMENTS`` / ``MAX_ATTR_CHARS``, enforced INDEPENDENTLY here
#: because a store file is an outside input by the time this module reads it.
MAX_RECTS = 150


#: This document's resolved frames, ``{"frames": {key: filename}}``.
#:
#: A ContextVar rather than a parameter for the reason ``mcp_handlers``'
#: ``_MOBILE_IMAGE_SPECS`` is one: the value is decided ONCE at the top of the
#: build and read in exactly one leaf (:func:`_shot_html`) that four call sites
#: reach through three intermediate builders, so threading it would rewrite five
#: signatures for a value none of them use. Two reports rendered concurrently
#: cannot see each other's frames.
_SHOTS: ContextVar = ContextVar("_SHOTS", default=None)


NOT_CAPTURED = "this screen was not captured"


def _bounds_of(element: object) -> tuple | None:
    """``[x1,y1,x2,y2]`` -> a normalised tuple, or None.

    Inverted bounds are SORTED rather than rejected: an app that reports
    ``[400,900,100,200]`` has still told us where its control is, and a negative
    width would otherwise reach the geometry.
    """
    body = element if isinstance(element, dict) else {}
    raw = body.get("bounds")
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    numbers = []
    for value in raw:
        if isinstance(value, bool):
            return None
        try:
            numbers.append(int(float(value)))
        except (TypeError, ValueError, OverflowError):
            return None
    left, right = sorted((numbers[0], numbers[2]))
    top, bottom = sorted((numbers[1], numbers[3]))
    return (left, top, right, bottom)


# The largest device extent this module accepts out of a store file. The WRITER
# cannot produce more -- `perception.parse_bounds` matches at most seven digits
# -- so a larger value in a store file did not come from a dump this server
# pruned. `scale_bounds` keeps its no-raise guarantee either way; this is the
# repo's "every cap is bounded from above" convention applied to a value whose
# own docstring calls it untrusted input.
MAX_DEVICE_EXTENT = 9999999


def _device_size(screen: object) -> tuple:
    """The device viewport: the stored display rectangle, or the elements' extent.

    Not a hardcoded phone size: a tablet AVD is legitimate. ``(0, 0)`` when the
    dump carries no usable geometry, which is what stops the divide.

    **The stored root is preferred because ``max()`` is not the viewport.** A
    ``RecyclerView``/``ScrollView`` reports its CONTENT height, so on a scrollable
    screen the lowest element bottom routinely exceeds the display -- and
    :func:`scale_bounds`, which scales by ``min(MAX_FRAME_W/dev_w, MAX_FRAME_H/dev_h)``,
    then shrinks EVERY element to fit a device taller than the real one. The
    visible result is every scrollable screen squeezed into the top of its frame.

    ``perception.prune`` stores that rectangle as ``root_bounds``: the device's
    own display, from ``adb.display_size``, oriented by the dump's ``rotation``
    -- NOT a window chosen out of the dump. Four rounds of choosing one are in
    docs/DECISIONS.md -> *The display size is not in the dump*. RIGHT and BOTTOM
    are the size: the origin is always ``(0, 0)``, so the extent measured from
    it is what :func:`scale_bounds` maps against.

    Where the device could not answer AND no depth-1 window was measurable, the
    panel is genuinely unknown and ``root_bounds`` is ``[]`` -- which since the
    panel stopped borrowing the frame's rule is reachable with a FULL element
    list, not just on an empty dump. So ``frame_bounds`` is read next: the
    rectangle ``prune`` actually filtered with, which is at least as large as
    everything laid out. It is not the display and does not claim to be; it is a
    scale the PRODUCER derived rather than one this function improvises.

    The ``max()`` walk survives below as the last resort for one caller that
    still happens: a screen stored by a build that wrote neither key. It must
    not become the ordinary path for an unknown panel -- it walks the pruned
    ELEMENTS, so on a scrollable screen it returns the container's content
    height, and that is the squeeze described above.

    This is a store file -- an outside input by the time it is read -- so a wrong
    type, a wrong length, a bool, a non-number, a negative origin or a zero-area
    rect must all DEGRADE to the fallback rather than raise or hand back a zero
    divisor. An INVERTED rect does not degrade: :func:`_bounds_of` sorts
    left/right and top/bottom deliberately, so ``[1080, 2400, 0, 0]`` normalises
    to ``(0, 0, 1080, 2400)`` and is accepted. That is the helper's existing,
    documented behaviour and it is pinned by test rather than changed here.
    """
    body = screen if isinstance(screen, dict) else {}
    # Priority order, one set of clauses: display, then filter frame, then the walk.
    for key in ("root_bounds", "frame_bounds"):
        size = _stored_extent(body.get(key))
        if size is not None:
            return size
    return _elements_extent(body.get("elements"))


def _stored_extent(value: object) -> tuple | None:
    """``(right, bottom)`` of a stored rectangle, or None when it is unusable."""
    rect = _bounds_of({"bounds": value})
    if (
        rect is not None
        and rect[0] >= 0
        and rect[1] >= 0
        and 0 < rect[2] <= MAX_DEVICE_EXTENT
        and 0 < rect[3] <= MAX_DEVICE_EXTENT
    ):
        # Each clause is reachable on its own, which is what makes it a
        # guard rather than decoration. `_bounds_of` SORTS, so
        # `rect[2] >= rect[0]` always holds -- but with the origin only
        # required to be non-negative, `[0,0,0,2400]` still reaches and
        # fails `rect[2] > 0`, and `[0,0,1080,0]` still reaches and fails
        # `rect[3] > 0`. A negative origin fails the first two and never
        # reaches either.
        return rect[2], rect[3]
    return None


def _elements_extent(elements: object) -> tuple:
    """The lowest-right corner over the elements: the last-resort device size."""
    width = 0
    height = 0
    for element in elements or []:
        box = _bounds_of(element)
        if box is None:
            continue
        width = max(width, box[2])
        height = max(height, box[3])
    return width, height


def _frame_scale(dev_w: int, dev_h: int) -> float:
    """THE scale factor for one device: device pixels -> frame pixels.

    One producer, because two consumers need the SAME float and not merely the
    same formula. :func:`_frame_box` multiplies it to size the box;
    :func:`scale_bounds` multiplies it to place every rect inside that box. When
    `scale_bounds` re-derived its own scale from the already-rounded box, the
    box's integer aspect differed from the device's by a fraction of a pixel and
    a full-screen element came out one pixel short of the frame it fills -- the
    same input, derived twice, drifting. Callers must guarantee both arguments
    are positive; `_frame_box`'s guard is what establishes that.
    """
    return min(MAX_FRAME_W / float(dev_w), MAX_FRAME_H / float(dev_h))


def _frame_box(dev_w: int, dev_h: int) -> tuple:
    """The rendered wireframe box for one device: its own aspect, contained
    inside the ``MAX_FRAME_W``x``MAX_FRAME_H`` ceiling.

    ONE producer of frame size -- :func:`scale_bounds` and :func:`wireframe`
    both call this rather than each deriving it, so the clamp and the drawn
    box can never disagree (the two-derivations trap this project has hit
    before). Guarantees: both returned values are ``int``, ``>= 1``, and
    ``<= `` their respective ceiling. A non-positive device (the guard this
    work owns -- see the mutation clause ratchet) falls back to the full
    ceiling box rather than dividing by zero or returning a degenerate one.
    """
    if dev_w <= 0 or dev_h <= 0:
        return (MAX_FRAME_W, MAX_FRAME_H)
    scale = _frame_scale(dev_w, dev_h)
    # TRUNCATE, exactly as `scale_bounds` does for w/h. The two must round the
    # same way or a full-device element lands a pixel short of its own frame:
    # round() here gave a 2208px-tall device a 450px box whose full-screen rect
    # scaled to 449. Same input, two derivations, one drifting -- the trap this
    # producer exists to close, reintroduced by the arithmetic rather than by a
    # second call site. Truncation is also the safe direction: the box can only
    # be smaller than the exact fit, never larger, so nothing paints outside it.
    frame_w = min(MAX_FRAME_W, max(1, int(dev_w * scale)))
    frame_h = min(MAX_FRAME_H, max(1, int(dev_h * scale)))
    return (frame_w, frame_h)


def scale_bounds(box: object, dev_w: int, dev_h: int) -> tuple | None:
    """Device pixels -> a rect inside this device's own frame, or None.

    Guarantees, unconditionally, for every tuple it returns: all four values are
    ``int``, ``x >= 0``, ``y >= 0``, ``x + w <= frame_w`` and ``y + h <=
    frame_h``, where ``frame_w, frame_h = _frame_box(dev_w, dev_h)`` and so
    ``frame_w <= MAX_FRAME_W`` and ``frame_h <= MAX_FRAME_H`` always. A
    malformed dump must not be able to paint outside the frame or raise.
    """
    try:
        left, top, right, bottom = (int(value) for value in box)
        left = min(max(0, left), int(dev_w))
        right = min(max(0, right), int(dev_w))
        top = min(max(0, top), int(dev_h))
        bottom = min(max(0, bottom), int(dev_h))
        # THE guard. A zero or negative device collapses every clamped box to
        # right <= left (or bottom <= top), so this one check is what refuses
        # it -- and it is the only reachable one. Two explicit zero-device
        # checks (here and in wireframe) sat in front of it and both graded
        # dead under mutation (round-14): removing either changed nothing.
        # The division below is reached only when right > left, which needs
        # dev_w > 0.
        if right <= left or bottom <= top:
            return None
        frame_w, frame_h = _frame_box(dev_w, dev_h)
        # The SAME float `_frame_box` sized the box with -- never a scale
        # re-derived from the rounded box, which drifts by a pixel on the axis
        # the ceiling did not constrain.
        scale = _frame_scale(dev_w, dev_h)
        x = int(left * scale)
        y = int(top * scale)
        w = max(1, int((right - left) * scale))
        h = max(1, int((bottom - top) * scale))
        x = min(x, frame_w - 1)
        y = min(y, frame_h - 1)
        w = min(w, frame_w - x)
        h = min(h, frame_h - y)
        return (x, y, w, h)
    except (TypeError, ValueError, OverflowError):
        return None


def _rect_of(element: object, placed: tuple) -> dict:
    """One drawn wireframe rectangle: scaled box, label and interaction kind."""
    holder = element if isinstance(element, dict) else {}
    label = _text(holder.get("text") or holder.get("desc") or holder.get("rid"), 40)
    kind = "plain"
    if holder.get("clickable"):
        kind = "tap"
    elif holder.get("editable"):
        kind = "edit"
    return {
        "x": placed[0],
        "y": placed[1],
        "w": placed[2],
        "h": placed[3],
        "label": label,
        "kind": kind,
    }


def wireframe(screen: object) -> dict:
    """``{"rects", "device", "scaled", "clipped", "outside"}`` for one pruned
    screen. ``clipped`` counts elements drawn as the sliver that fits the
    display; ``outside`` counts elements with area but none of it on the
    display, which are not drawn; the page discloses both.

    ``scaled`` is False when the dump had no usable geometry -- the report then
    draws a labelled empty frame rather than a plausible-looking wrong one.
    """
    body = screen if isinstance(screen, dict) else {}
    dev_w, dev_h = _device_size(body)
    rects, clipped, outside = _place_elements(body.get("elements"), dev_w, dev_h)
    # `scaled` is a claim about the PICTURE, not about the geometry that went
    # into it: this function's contract is that a False draws a labelled empty
    # frame rather than a plausible-looking wrong one. A screen whose elements
    # all lie outside the display -- reachable since the visibility frame was
    # widened past it -- scales every one of them to nothing and would otherwise
    # return an empty frame claiming to be a real one.
    frame_w, frame_h = _frame_box(dev_w, dev_h)
    return {
        "rects": rects,
        "device": [dev_w, dev_h],
        "scaled": bool(rects),
        "clipped": clipped,
        "outside": outside,
        "frame_w": frame_w,
        "frame_h": frame_h,
    }


def _place_elements(elements: object, dev_w: int, dev_h: int) -> tuple:
    """``(rects, clipped, outside)`` for the elements scaled onto the frame."""
    # No zero-device check HERE: `scale_bounds` refuses a zero device for every
    # box and the loop then produces no rects, which is the same frame the
    # removed early return built by hand. Two checks for one condition was a
    # second copy waiting to drift (round-13 review, R3/R6).
    rects = []
    clipped = 0
    outside = 0
    for element in list(elements or [])[:MAX_RECTS]:
        box = _bounds_of(element)
        if box is None:
            continue
        # Two names for two facts, counted BEFORE the scale decides whether
        # anything is drawn (round-14 review, H1: counting after the
        # `placed is None` skip omitted every element that lay entirely off
        # the display, so a screen that lost 2 of 4 elements said "1").
        #   clipped -- has area inside the display but reaches past it; drawn
        #              as the sliver that fits.
        #   outside -- has area, none of it on the display; not drawn at all.
        # A degenerate (zero-area) box is neither: there is nothing to draw
        # and nothing was lost.
        has_area = box[2] > box[0] and box[3] > box[1]
        past_edge = box[0] < 0 or box[1] < 0 or box[2] > dev_w or box[3] > dev_h
        placed = scale_bounds(box, dev_w, dev_h)
        if placed is None:
            if has_area and dev_w > 0 and dev_h > 0:
                outside += 1
            continue
        if past_edge:
            clipped += 1
        rects.append(_rect_of(element, placed))
    return rects, clipped, outside


#: Said instead of drawing the wrong look. "Not captured" would be false here:
#: the screen WAS captured, several times, and this frame does not know which
#: of them it means.
AMBIGUOUS_LOOK = (
    "This screen was seen more than once and this frame does not record which "
    "look it means, so nothing is drawn rather than the most recent one."
)


def _look_is_ambiguous(ident: str, library: dict) -> bool:
    """Does this BARE screen id stand for more than one stored look?

    False for an observation key -- that already names its look, and the dash it
    carries is what tells the two apart. Counting the observations belonging to
    this screen is what lets a screen seen exactly once keep rendering, instead
    of being suppressed by a guard too coarse to tell the cases apart.

    THE DASH FAST PATH IS GONE, and its removal is the fix. It returned False
    for any ident carrying a dash, which is right for an observation key and
    WRONG for the screen id ``_document`` concedes is admissible: `_RUN_ID_RE`
    accepts ``aabbccdd0011-000000000000``, and such an id with two stored looks
    was answered "unambiguous" by the fast path and "ambiguous" by the count --
    one question derived twice inside one function, disagreeing. The count is
    the only derivation now. It still answers False for a real observation key,
    because the prefix it scans for is that key plus another dash and no library
    entry can start with it, so nothing about the ordinary case changed.
    """
    if not isinstance(ident, str):
        return False
    prefix = ident + "-"
    seen = 0
    for key in library if isinstance(library, dict) else {}:
        if isinstance(key, str) and key.startswith(prefix):
            seen += 1
            if seen > 1:
                return True
    return False


#: The three answers a frame key can get from the merged library. ONE producer,
#: one meaning, EVERY consumer: `_frame_html` and `_phone_html` both ask
#: :func:`_resolve_look` and neither derives the question again. Three review
#: rounds were three consumers each deriving it separately, and each got it
#: wrong somewhere else.
LOOK_EXACT = "exact"


LOOK_UNCAPTURED = "uncaptured"


LOOK_AMBIGUOUS = "ambiguous"


def _resolve_look(screen_id: object, screens: object) -> tuple[str, dict | None, str]:
    """A frame key against the merged library: ``(ident, screen or None, verdict)``.

    THE resolver. A bare screen id says WHICH screen and cannot say WHAT was on
    it: the screen library holds the LAST look, and a screen observed more than
    once has several. Resolving a frame against it draws the newest content
    under whatever caption the caller wrote -- on a conversation, a reply the app
    had not yet produced, shown to a non-technical tester as evidence of what
    their own action did.

    ``LOOK_AMBIGUOUS`` means TWO OR MORE stored looks. Exactly one is not
    ambiguous -- the bare id and that single observation are the same bytes -- so
    a settings walk and every single-look screen resolve as they always did.
    That is what keeps this a guard and not a regression.

    The verdict is returned rather than acted on because the two consumers
    render it differently -- the fold draws an empty frame, the phone an empty
    handset -- and the point of this function is that neither of them decides
    the QUESTION.
    """
    ident = _text(screen_id, 40)
    library = screens if isinstance(screens, dict) else {}
    if not ident:
        return "", None, LOOK_UNCAPTURED
    screen = library.get(ident)
    if not isinstance(screen, dict):
        return ident, None, LOOK_UNCAPTURED
    if _look_is_ambiguous(ident, library):
        return ident, None, LOOK_AMBIGUOUS
    return ident, screen, LOOK_EXACT


#: The ``src`` prefix every ``img`` on this page carries, and THE definition of it.
#:
#: A FOLDER-RELATIVE PATH, not a ``data:`` payload. The index lives in ``report/``
#: inside the run directory and the PNGs live in ``../shots/``, so the whole folder
#: travels as one artifact -- which is already the documented contract (the
#: recordings under ``report/media/`` have always worked this way). A lone
#: ``index.html``, separated from its folder, shows no screens; the page says so in
#: its own standfirst rather than rendering broken images silently.
#:
#: Declared here, in the emitter, and IMPORTED by the guard
#: (``report_selfcheck.SHOT_SRC = report.SHOT_SRC``) rather than restated there.
#: The direction is forced: ``report_selfcheck`` already does
#: ``from tools.mobile import report``, so the reverse import would be a cycle.
#: One object, one meaning -- this very change inverted the MEANING of the value
#: while the name stayed, and every consumer that bound the constant rather than
#: the literal needed no edit at all. That is the property, demonstrated.
SHOT_SRC = "../" + run_store.SHOTS_DIR + "/"

#: An observation for which NOTHING was ever stored.
#:
#: A DIFFERENT name and different words from the note below, on purpose: "no
#: picture was ever taken" and "a picture exists and this page declined it" are
#: different facts about the run and only one of them is worth investigating.
#: Collapsing them is the half-wired-sentinel failure this repository has
#: already paid for -- the same three-state rule ``tools/mobile/screenshot.py``
#: states for the packet's own note.
SHOT_MISSING_NOTE = (
    "No picture of this screen was stored: the capture did not succeed on that "
    "turn, or this run is older than stored frames. The drawing below is "
    "composed from the element list, which is all this page has."
)


def _shots(run_id: object, library: object) -> dict:
    """``{"frames": {key: filename}}``. Never raises.

    ONE producer for every picture on this page. It answers exactly one question --
    "does a non-empty PNG exist on disk for this observation?" -- by reading the
    file, so the page can never claim a picture that is not there.

    *library* is the MERGED book :func:`_merged_library` builds -- screens AND
    observations -- because that is what a frame resolves against, and a frame the
    page cannot resolve is a frame no picture belongs under.

    THERE IS NO BUDGET ANY MORE. Nothing is inlined, so the page costs the same
    whether it references one picture or two hundred, and a "this report declined
    to show it" state has no producer left. It was DELETED rather than kept as an
    always-empty list: a sentence with no producer is a fabricated absence, and an
    always-empty branch is how a dead state survives a rewrite.
    """
    frames: dict = {}
    try:
        book = library if isinstance(library, dict) else {}
        for ident in sorted(str(key) for key in book):
            data = (run_store.read_shot(str(run_id), ident) or {}).get("content")
            if not isinstance(data, (bytes, bytearray)) or not data:
                continue
            frames[ident] = ident + ".png"
    except Exception:  # never-raise: a picture is not a verdict
        logger.exception("mobile.report._shots failed")
    return {"frames": frames}


def _shot_html(ident: str) -> str:
    """The stored PNG of *ident*, referenced by path, or the honest note for its absence.

    *ident* is what :func:`_resolve_look` RESOLVED, and this function is called only
    on its ``LOOK_EXACT`` answer. That is load-bearing: under ``LOOK_AMBIGUOUS`` the
    key is a bare screen id with two or more stored looks behind it, and a picture
    drawn there would be one arbitrary look captioned as the step's own -- the
    fabricated-evidence defect ``_resolve_look`` exists to stop, in a form a tester
    is even less able to question because it is a photograph.

    A PATH and never a ``data:`` URI: see :data:`SHOT_SRC`. The self-check enforces
    that every ``img`` on the page starts with that prefix and climbs exactly one
    level -- ``report_selfcheck._pin_links`` checks IDENTITY, as it already does for
    the shell's one script and for every clip source.

    The ``<img>`` is emitted ONLY from a non-empty entry in ``frames``, which only a
    non-empty file on disk can produce, so a capture that failed can never leave
    this page claiming a picture exists.
    """
    book = _SHOTS.get() or {}
    filename = str((book.get("frames") or {}).get(ident) or "")
    if filename:
        return (
            '<figure class="shot"><img alt="The screen as the device drew it" '
            'loading="lazy" src="' + SHOT_SRC + filename + '"></figure>'
        )
    return '<p class="wirenote shotnote">' + esc(SHOT_MISSING_NOTE, 300) + "</p>"


def _frame_html(screen_id: object, screens: object) -> str:
    """One frame, or an honest empty one when the screen was not stored."""
    ident, screen, verdict = _resolve_look(screen_id, screens)
    if not ident:
        return (
            '<div class="frame missing"><p class="wirenote">'
            + esc(NOT_CAPTURED)
            + "</p></div>"
        )
    if verdict == LOOK_UNCAPTURED:
        return _missing_frame(ident, NOT_CAPTURED)
    if verdict == LOOK_AMBIGUOUS:
        # THE INVARIANT, DECIDED IN `_resolve_look` AND HONOURED HERE.
        #
        # A bare screen id says WHICH screen. It cannot say WHAT was on it: the
        # screen library holds the LAST look, and a screen observed more than
        # once has several. Resolving a frame against it draws the newest
        # content under whatever caption the caller wrote -- on a conversation,
        # a reply the app had not yet produced, shown to a non-technical tester
        # as evidence of what their action did.
        #
        # This is the THIRD consumer to make that mistake: the sequence row's
        # "Screen after", the overview's opening-screen count, and the case
        # card's "Last screen" -- each found in a separate review round, each
        # patched separately, and two of them introduced by the change that
        # created the second library in the first place. So the rule stops being
        # something every caller must remember and becomes something no caller
        # can get wrong.
        #
        # Ambiguous means TWO OR MORE stored looks. Exactly one is not
        # ambiguous -- the bare id and that single observation are the same
        # bytes -- so a settings walk and every single-look screen render as
        # they always did. That is what keeps this a guard and not a regression.
        return _missing_frame(ident, AMBIGUOUS_LOOK)
    return _drawn_frame(ident, wireframe(screen))


def _missing_frame(ident: str, note: str) -> str:
    """An empty frame for *ident* carrying *note* as its reason."""
    return (
        '<div class="frame missing" data-screen="'
        + esc(ident, 40)
        + '"><p class="wirenote">'
        + esc(note)
        + "</p></div>"
    )


def _rect_html(rect: dict) -> str:
    """One element rectangle of the wireframe."""
    return (
        '<div class="rect '
        + esc(rect["kind"], 12)
        + '" style="left:'
        + str(int(rect["x"]))
        + "px;top:"
        + str(int(rect["y"]))
        + "px;width:"
        + str(int(rect["w"]))
        + "px;height:"
        + str(int(rect["h"]))
        + 'px" title="'
        + esc(rect["label"], 40)
        + '" dir="auto">'
        + esc(rect["label"], 40)
        + "</div>"
    )


def _drawn_frame(ident: str, frame: dict) -> str:
    """The wireframe of one stored screen: its rectangles and geometry note."""
    rects = "".join(_rect_html(rect) for rect in frame["rects"])
    return (
        '<div class="frame" style="width:'
        + str(int(frame["frame_w"]))
        + "px;height:"
        + str(int(frame["frame_h"]))
        + 'px" data-screen="'
        + esc(ident, 40)
        + '" data-rects="'
        + str(len(frame["rects"]))
        + '" data-scaled="'
        + ("1" if frame["scaled"] else "0")
        + '" data-clipped="'
        + str(int(frame.get("clipped") or 0))
        + '" data-outside="'
        + str(int(frame.get("outside") or 0))
        + '">'
        + rects
        + _geometry_note(frame)
        + "</div>"
    )


def _geometry_note(frame: dict) -> str:
    """The frame's disclosure of what the picture could not show, or ''.

    Rendered as ``.frame .wirenote.geom`` -- the shell styles it (round-14
    review, H3: the first version emitted an unstyled class in-flow under the
    absolutely positioned rects, where nobody could read it) and pins it to the
    frame's foot above the rects. Both counts are ints this module computed, so
    the interpolation is the one kind that needs no escaping.
    """
    clipped = int(frame.get("clipped") or 0)
    outside = int(frame.get("outside") or 0)
    if not clipped and not outside:
        return ""
    parts = []
    if clipped:
        parts.append(
            str(clipped) + " element(s) extend past the display and are drawn clipped"
        )
    if outside:
        parts.append(
            str(outside) + " element(s) lie entirely off the display and are not drawn"
        )
    return '<p class="wirenote geom">' + "; ".join(parts) + "</p>"


def _element_map_fold(screen_id: object, screens: object) -> str:
    """The folded element-rectangle wireframe shown under the phone."""
    return (
        '<details class="sec"><summary><span class="chev" aria-hidden="true"></span>'
        'Element map<span class="mt">the same screen as its element rectangles</span></summary>'
        '<div class="secbody"><div class="phone wire"><div class="ph-scroll">'
        + _frame_html(screen_id, screens)
        + "</div></div>"
        + _WIRE_LEGEND
        + "</div></details>"
    )


def _empty_phone(verdict: str) -> str:
    """An empty handset that says why no look is drawn."""
    return (
        '<div class="phone" dir="auto"><div class="ph-scroll"><div class="ph-empty">'
        + esc(AMBIGUOUS_LOOK if verdict == LOOK_AMBIGUOUS else NOT_CAPTURED)
        + "</div></div></div>"
    )


def _phone_html(
    title: str, sub: str, screen_id: object, screens: object, app: str
) -> str:
    """The screen as the app drew it, with the element map folded beneath it.

    Both pictures come from the same pruned dump. ``screen_phone.compose`` reads
    the elements' text, labels and bounds into the app bar, bubbles, cards, chips
    and composer the user saw; the wireframe keeps the exact rectangles for
    anyone who needs them. Above both sits the PNG the lane captured at that
    observation, when one was stored and this page could afford it; where it
    could not, the note in its place says which of those two things happened.
    """
    ident, screen, verdict = _resolve_look(screen_id, screens)
    if screen is not None:
        # The photograph first, the composition under it: the drawing is an
        # annotation of the screen, not a substitute for it, and a reader who
        # sees the real thing first can tell which is which.
        #
        # INSIDE this branch and nowhere else. `_resolve_look` has already said
        # this key names ONE stored look; on its `LOOK_AMBIGUOUS` answer the
        # else-branch below draws an empty handset and says the look is
        # unknowable, and a photograph hung above that sentence would contradict
        # it with the one kind of evidence a tester cannot argue with.
        drawn = _shot_html(ident) + screen_phone.compose(screen, esc=esc, app=app)
        fold = _element_map_fold(screen_id, screens)
    else:
        # THE PICTURE GETS THE SAME ANSWER AS THE FOLD BENEATH IT. Until this
        # change the fold said the look was unknowable while the phone above it
        # drew the newest one: one page contradicting itself, because this
        # function resolved the library itself and the round-two guard governed
        # only `_frame_html`, which is the fold. `ph-empty` is an existing rule
        # in ``report_shell.html`` -- no new class name is introduced, because a
        # class with no rule renders unstyled with nothing failing.
        drawn = _empty_phone(verdict)
        fold = ""
    return (
        '<div class="phone-col" data-look="'
        + esc(verdict, 16)
        + '" data-key="'
        + esc(ident, 40)
        + '"><h4>'
        + esc(title, 40)
        + '<span class="mt">'
        + esc(sub, 80)
        + "</span></h4>"
        + drawn
        + fold
        + "</div>"
    )


_WIRE_LEGEND = (
    '<ul class="wirelegend"><li><i class="sw tap"></i>tappable</li>'
    '<li><i class="sw edit"></i>editable</li><li><i class="sw"></i>other element</li></ul>'
)


def _seq_frames(rows: object, screens: object) -> dict:
    """``{trace index: frame html}`` for the merged app-evidence stream.

    The Run sequence has TWO renderers and only one of them is ``_seq_rows``.
    When a case has app evidence, ``_card_html`` renders
    ``ev_render.sequence_items(...)`` INSTEAD -- that stream REPLACES the seq
    rows whole -- so a picture threaded only into ``_seq_rows`` renders on no
    such run at all, while this page's own prose promises one for every step.
    Two render sites closed out of three is a promise that is false exactly
    where nobody looked.

    The indices are the ones ``sequence_items`` already aligns its trace against
    (``rows[i]``), so this is the SAME alignment rather than a second one.

    Built by :func:`_shot_html`, THE producer of this markup, and only on
    :func:`_resolve_look`'s ``LOOK_EXACT`` answer -- the same gate
    :func:`_phone_html` honours, so the merged stream cannot show one arbitrary
    look of an ambiguous screen as the step's own.
    """
    out: dict = {}
    for index, row in enumerate(list(rows or [])):
        if not isinstance(row, dict):
            continue
        ident, screen, verdict = _resolve_look(row.get("after"), screens)
        if screen is None or verdict != LOOK_EXACT:
            continue
        html = _shot_html(ident)
        if html:
            out[index] = html
    return out


#: ``sizes`` for a step picture and for a filmstrip thumbnail.
STEP_SIZES = "(max-width: 600px) 44vw, 240px"


#: The ops whose target is an element on the screen before the step.
TARGET_OPS = ("tap", "long_press", "type", "clear")


def _tap_overlay(target_id: object, screen: object) -> str:
    """The acted-on element's box and a tap dot, in CSS percentages, or ``""``."""
    tid = str(target_id or "")
    body = screen if isinstance(screen, dict) else {}
    if not tid:
        return ""
    for element in list(body.get("elements") or []):
        if isinstance(element, dict) and str(element.get("id") or "") == tid:
            dev_w, dev_h = _device_size(body)
            box = report_images.overlay_style(_bounds_of(element), dev_w, dev_h)
            if not box:
                return ""
            return (
                '<span class="tapbox" aria-hidden="true" style="left:{left}%;top:{top}%;'
                'width:{width}%;height:{height}%"></span><span class="tapdot" '
                'aria-hidden="true" style="left:{dot_x}%;top:{dot_y}%"></span>'
            ).format(**{k: float(v) for k, v in box.items()})
    return ""


def _look_pic(
    key: object,
    page: _PageData,
    caption: str,
    target_id: str = "",
    note: str = "",
) -> str:
    """One moment of a step: the device's picture, the drawing, or why neither.

    The note stands IN PLACE of a picture, never beside one. The drawing is
    composed only when no picture was stored for an exact look.

    Every figure is stamped with what `_resolve_look` returned: ``data-look``
    the verdict and ``data-screen`` the key it resolved. The page-level sweep in
    ``test_mobile_screen_observations`` reads those, so a caller that resolved
    the library itself would be graded on the day it was written.
    """
    ident, screen, verdict = _resolve_look(key, page.library)
    head = (
        '<figure class="pic" data-look="'
        + esc(verdict, 16)
        + '"'
        + ((' data-screen="' + esc(ident, 40) + '"') if ident else "")
        + ">"
    )
    label = esc(caption, 40) + (
        ('<span class="picsub">' + esc(note, 80) + "</span>") if note else ""
    )
    if screen is not None and verdict == LOOK_EXACT:
        return head + _exact_pic_body(
            (ident, screen), caption, page.app, target_id, label
        )
    gap = AMBIGUOUS_LOOK if verdict == LOOK_AMBIGUOUS else NOT_CAPTURED
    return (
        head
        + '<p class="picnote">'
        + esc(gap, 300)
        + "</p><figcaption>"
        + label
        + "</figcaption></figure>"
    )


def _exact_pic_body(
    look: tuple, caption: str, app: str, target_id: str, label: str
) -> str:
    """The stored picture with its tap overlay, else the drawing of the screen.

    ``look`` is ``(ident, screen)`` as `_resolve_look` returned them.
    """
    ident, screen = look
    filename = str(((_SHOTS.get() or {}).get("frames") or {}).get(ident) or "")
    book = _IMAGES.get()
    if filename and book is not None:
        return (
            '<div class="picbox">'
            + _img_html(
                "../" + run_store.SHOTS_DIR,
                filename,
                book["shots_dir"] / filename,
                caption + ": the screen as the device drew it",
                STEP_SIZES,
            )
            + _tap_overlay(target_id, screen)
            + "</div><figcaption>"
            + label
            + "</figcaption></figure>"
        )
    return (
        '<div class="picdraw">'
        + screen_phone.compose(screen, esc=esc, app=app)
        + "</div><figcaption>"
        + label
        + " · drawn from the element list, no picture stored"
        + "</figcaption></figure>"
    )


def _same_gap(one: tuple, two: tuple) -> bool:
    """Both looks resolved to no picture, with the same verdict."""
    return one[2] == two[2] and one[2] != LOOK_EXACT


def _step_pics(row: dict, library: object, app: str, after_look: tuple) -> str:
    """The Before and After pictures of one step, or one shared note for both."""
    before = row.get("before_obs") or row.get("before")
    after = row.get("after_obs") or row.get("after")
    target = row.get("target_id") if row.get("op") in TARGET_OPS else ""
    page = _PageData(library=library, app=app)
    if after_look:
        return (_look_pic(before, page, "Before", target) if before else "") + (
            _look_pic(after_look[0], page, "After", note=after_look[1])
        )
    if before and (
        before == after
        or (
            after
            and _same_gap(_resolve_look(before, library), _resolve_look(after, library))
        )
    ):
        # Neither end has a picture and both miss it for the same reason: one
        # note for the pair, not the same sentence twice side by side.
        return _look_pic(before, page, "Before and after", target)
    return (_look_pic(before, page, "Before", target) if before else "") + (
        _look_pic(after, page, "After") if after else ""
    )
