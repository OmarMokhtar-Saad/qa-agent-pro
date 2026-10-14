"""The long contract of ``perception_display._display_rect``, attached as its ``__doc__``."""

from __future__ import annotations

# The full contract of `_display_rect`, kept in its own module and attached as
# its ``__doc__`` there so the function body stays within the length limit.
_DISPLAY_RECT_DOC = """``(display, frame, axes)`` -- the screen, the region an element may
    occupy, and what is known of the screen PER AXIS.

    **These are two answers, not one, and conflating them is what this function
    exists to stop.** They differ only when the dump lays something out beyond
    the display the device reported, and that disagreement is unresolvable from
    here: either the device size is stale, or the app drew off-screen. So each
    consumer gets the answer its own failure mode requires.

    * ``display`` -- what the device says it is showing, rotated by the dump's
      own ``rotation``, or, where the device could not say, the union of the
      dump's depth-1 WINDOWS. This is the panel: the report's SCALE, and the
      rectangle actions are clamped to. A window is a display-sized thing and a
      child is not, which is why an overhanging ROW must not widen it -- a row
      dragged out to its own far edge would turn the executor's swipe clamp into
      a no-op and send a gesture the device silently ignores. It is ``None``
      when neither source could answer, and that is an ANSWER rather than a
      hole: the panel is derived from the panel's own node set or NOT AT ALL,
      because a fallback that borrows the frame's rule is the overhanging row
      declaring the screen again. Both axes come from one decision over that one
      set for the same reason.
    * ``frame`` -- ``display`` widened to cover everything the dump lays out.
      This is the off-screen VISIBILITY FILTER. Getting it too small deletes the
      tester's screen from the packet the model plans with and the destructive
      guard reads, silently, with ``truncated`` still False.

    The two answers diverging is the point, and it took a second consumer to see
    it. An earlier version derived BOTH from every laid-out node, on the
    reasoning that "which node counts" is the question four defective rounds
    came from. That is right for the frame and wrong for the panel: it let a
    single overhanging row declare the screen 3000px wide, and the gesture clamp
    downstream — which had its own tests — went green while issuing swipes off
    the panel.

    The widening is not a nicety. A display cached against a recycled emulator
    serial, a foldable measured before it folded, a `wm size` override set
    mid-session: each hands back a rectangle SMALLER than the screen the dump
    describes, and a review demonstrated a `Delete account` control vanishing
    from a tablet's packet under a stale phone size while the dump-derived
    fallback got the same dump right. An authoritative-looking value that is
    trusted further than the evidence it must contain is worse than no value.

    So the invariant is one line and holds on every path: **the frame is never
    smaller than what the dump lays out.**

    Say plainly what that costs, because it is not free: since the frame covers
    everything laid out, the off-screen filter below no longer DROPS anything
    laid out at a positive coordinate. It still drops a node placed entirely at
    negative coordinates, and that is now its whole job. A window laid out past
    the display therefore reaches the model as an element it may believe it can
    tap.

    That is the direction to fail in, and the trade is not close. An over-reach
    carries a row the tester cannot see -- still judged by the destructive guard,
    still flagged by ``truncated`` -- and a widened element set makes the guard
    MORE likely to stop, which is the safe way to be wrong. An under-reach
    deletes the row the tester is about to tap, silently, and leaves the guard
    nothing to refuse. Every defect this code has had was an under-reach.

    **The over-reached rows share the element budget, and that is a cost, not a
    containment.** Saying they are "counted against ``MAX_ELEMENTS``" reads like
    a bound and is the opposite: an adjacent pager page listed first can fill the
    cap and evict the control the tester is looking at. :func:`prune` therefore
    spends the budget on what intersects the DISPLAY first, and gives the
    remainder to what does not.

    Note where the cost actually lands. When the dump fits inside the display --
    the ordinary case -- the frame IS the display and nothing changes. The
    widening only happens when the dump and the device DISAGREE, which is exactly
    the case where we cannot tell a stale display from an app drawing off-screen,
    and so exactly the case where guessing is what we must not do.

    ``display`` is device output and untrusted: exactly two values, bools
    rejected before ``int()`` (``int(True)`` is a 1-pixel screen), each within
    :data:`MAX_DISPLAY_EXTENT`. Anything else, and the panel falls to the window
    walk -- never to the dump extent, which is the frame's rule and not the
    panel's.

    Returns ``(panel, frame, axes)``. *panel* is the rectangle when BOTH axes are
    known and ``None`` otherwise, because a scale must be a whole rectangle or
    absent. *axes* is what is known PER AXIS, ``0`` for one this walk could not
    answer -- the gesture clamp can bind one axis and leave the other, and
    forcing that through the rectangle is what made a window laid out above the
    display lose a clamp it should have had. *frame* is ``None`` only when no
    positive-area node has a positive right or bottom edge.
    """
