"""Screen perception: the uiautomator XML dump, pruned to what a model needs.

**The dump is attacker-influenced content.** Any app on the device controls the
``text``, ``content-desc`` and ``resource-id`` of its own nodes, and a malicious
or merely careless app can put anything at all in them -- including markup that
looks like ours. Three consequences, each pinned by a test:

1. **No DOCTYPE, no ENTITY.** ``xml.etree.ElementTree`` does not fetch external
   entities, but it DOES expand internal ones, which is the billion-laughs
   amplification. ``defusedxml`` would close that for us; it is not a dependency
   of this project and the programme contract forbids adding one. So the parse
   refuses outright when the document declares either -- a real uiautomator dump
   never does, so nothing legitimate is lost.
2. **Byte cap BEFORE parsing.** ``MAX_DUMP_BYTES`` is checked against the encoded
   length before a parser ever sees the string, because a cap applied afterwards
   is not a cap.
3. **Every attribute is untrusted text.** Attribute values are length-capped,
   control characters are stripped, and anything that could be mistaken for our
   own prompt scaffolding (an ``<untrusted_content>`` tag, the ``_GUARD``
   security-note opener) is neutralised HERE -- ``untrusted.wrap_untrusted``
   strips the tags but knows nothing about the guard note, and a screen that
   could forge one would be talking directly to the tester's model.

**An element id is CONTENT, not a row number.** ``prune`` derives every id from
that element's own class, text, description, resource id and bounds
(:func:`element_id`), so an id the model planned against one dump can never
silently resolve to a DIFFERENT element on the next one. See that function for
the tap-the-wrong-widget defect this closes.

Nothing here raises; every public function returns ``{"error", "content"}``.
"""

from __future__ import annotations

import hashlib
import logging
import re
from xml.etree import ElementTree

from tools.untrusted import _GUARD, wrap_untrusted

logger = logging.getLogger(__name__)

# Matched against the TRANSPORT cap in ``adb.uiautomator_dump``. Both exist on
# purpose: the transport one stops a huge dump from being carried at all, this
# one stops a huge string reaching a parser by any other route (a replayed
# checkpoint, a test fixture, a future file-backed dump).
MAX_DUMP_BYTES = 4 * 1024 * 1024

# The packet cap. 150 elements is roughly three screens' worth of controls; the
# reason for a cap at all is that the packet goes to the tester's own model and
# a 900-node ScrollView would crowd out the case it is meant to execute.
MAX_ELEMENTS = 150

# HEADROOM on the packet, for elements the panel puts off-display. They are
# carried at all only because the panel is an estimate that can be wrong -- a
# stale device size classifies the tester's own controls as off-display -- so
# off-panel crowding must never delete an on-panel control.
#
# Four passes at spending ONE budget between the two classes each fixed the
# previous pass's worst case and created a new one further out, because at a
# fixed cap "every on-panel element survives" and "every element the dump listed
# first survives" are jointly unsatisfiable -- a counting fact, not a
# rule-design problem. So the TOTAL is what grew, and `prune` walks in DOCUMENT
# order: nothing downstream sees a reordering, and `element_seed`/`hash`, the
# rendered lines the model plans from, and the guard's equal-area tie-break all
# read the dump's own order, as they did before any of this.
#
# It bounds the TOTAL and is NOT a cap on the off-panel class. Capping that
# class was measured deleting 25 of the tester's own controls out of a
# 110-element tablet dump under a stale panel -- a dump today's code carries
# whole -- because the class we have decided we cannot trust the membership of
# is the one place a bound cannot be justified by the classification: if the
# panel is wrong, that class IS the screen. With the total bounded instead, no
# dump loses more elements than it loses today, whatever the panel says.
MAX_PACKET_HEADROOM = MAX_ELEMENTS // 2

# Per-attribute cap. Long enough for a paragraph of on-screen copy, short enough
# that one hostile node cannot dominate the packet.
MAX_ATTR_CHARS = 200

# A repeated string shorter than this is printed in full at every element rather
# than replaced by an `@id` reference. A reference costs four or five characters
# plus a lookup the reader has to make, so below this length the dedupe spends
# legibility to save nothing. The harmful direction is DOWNWARD -- see the
# CEILINGS row in tests/mobile/test_mobile_bounds_upper.py, which names the floor
# test that binds it.
MIN_DEDUPE_CHARS = 24

# How much of a control's label the `fields` / `controls` SUMMARIES print. The
# element line beneath still prints it in full, so this can never hide a string
# -- only fail to index it. Never applied in a way that lets two distinct
# controls read the same: see `_summary_labels`.
MAX_SUMMARY_LABEL_CHARS = 48

#: Emitted in the block head, and ONLY when a reference is actually present, so
#: a screen with no repetition pays nothing for the notation. It lives in the
#: SCREEN BLOCK rather than the system prompt because the screen block is on
#: every packet and the system prompt, since the per-chat contract landed, is
#: not.
REFERENCE_LEGEND = (
    "note: `text=@e7` means this element's text is the SAME string that element "
    "e7 prints in full above -- printed once to keep this screen readable. To "
    "act on such an element, target it by its own id (or its rid or role); to "
    "target it by text, copy the string from the line it is printed on. A "
    "`label=` is never abbreviated this way."
)

#: The same fact in one line, for a block that cannot afford the paragraph.
#:
#: A reference and its explanation travel TOGETHER -- an unexplained `@e7` is
#: worse than the bytes it saves. The first attempt at that rule dropped the
#: REFERENCES instead when the legend would not fit, and paid for it in the
#: currency that matters: measured 1 element line where the referenced form fit
#: 11, because plain lines are three times longer. Degrading the explanation is
#: cheap and degrading the notation is not, so this exists and is tried before
#: either is abandoned.
REFERENCE_LEGEND_SHORT = (
    "note: `text=@e7` means the same string element e7 prints in full above; "
    "target such an element by its id."
)

# Prompt-block cap handed to ``wrap_untrusted``.
MAX_BLOCK_CHARS = 12000

# A node with no text, no description, no resource id and no affordance is pure
# layout scaffolding: it cannot be a target and it cannot be asserted on.
EDITABLE_CLASS_HINTS = (
    "EditText",
    "AutoCompleteTextView",
    "SearchView",
    "TextInputEditText",
)

_DECL_RE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_BOUNDS_RE = re.compile(r"\[(-?\d{1,7}),(-?\d{1,7})\]\[(-?\d{1,7}),(-?\d{1,7})\]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RE = re.compile(r"[ \t]{2,}")

# A derived marker must never be longer than this, so a pathological guard
# cannot turn every screen into one giant redaction.
MAX_GUARD_SENTINEL_CHARS = 60

# The largest display dimension accepted from the device. `adb.display_size`
# parses it out of `wm size`, which is device output rather than something this
# process computed, so it is bounded like every other untrusted number here. It
# matches the seven digits `_BOUNDS_RE` allows a dump to carry, so a display and
# the windows on it cannot disagree about what a representable coordinate is.
MAX_DISPLAY_EXTENT = 9999999


def _guard_sentinel() -> str:
    """The opening LABEL of ``untrusted._GUARD``, derived rather than copied.

    This used to be a hardcoded copy of that label, and that was a silent-drift
    defect rather than a style problem. ``_GUARD`` is owned by
    ``tools/untrusted.py``; a rewording there would leave this module
    neutralising a phrase the product no longer sends, the forged-guard hole
    would reopen, and **no test would notice**, because a test pinning the old
    copy still passes. Note that this docstring deliberately does not spell the
    current label either: a comment naming it would defeat any future grep for
    a hardcoded guard phrase.

    So the marker is taken as a PREFIX of the live ``_GUARD``. Both branches
    below slice ``text``, which makes "the marker is a substring of the live
    guard" true BY CONSTRUCTION rather than by anyone remembering.
    ``test_the_guard_sentinel_is_derived_from_the_live_guard_not_copied``
    asserts that property, so a divergence fails CI instead of failing quietly.
    """
    text = str(_GUARD or "").strip()
    head, separator, _rest = text.partition(":")
    if separator and 0 < len(head) <= MAX_GUARD_SENTINEL_CHARS:
        return head + separator
    return text[:MAX_GUARD_SENTINEL_CHARS]


GUARD_SENTINEL = _guard_sentinel()

# Everything a screen must not be able to say to a model. ``untrusted_content``
# duplicates ``wrap_untrusted``'s own strip deliberately: the pruned dict is also
# written to the run store and rendered into the report, neither of which goes
# through ``wrap_untrusted``. ``GUARD_SENTINEL`` covers the other half, which
# ``wrap_untrusted`` does NOT strip -- measured, not assumed.
# Falsy markers are DROPPED. An empty or whitespace-only ``_GUARD`` (a refactor
# that assembles it at call time, or renames it to a falsy default) makes
# GUARD_SENTINEL "", and "" is a substring of every string -- the neutraliser
# would then be inserted between every character of every attribute and the
# length cap would discard the real content, leaving a screen where nothing
# resolves and no assert matches, with no refusal to explain it. The
# pathological-LONG case was already handled by MAX_GUARD_SENTINEL_CHARS; this
# is the other end of the same edit.
GUARD_MARKERS = tuple(
    marker
    for marker in (
        "untrusted_content",
        GUARD_SENTINEL,
    )
    if marker
)
NEUTRALIZED = "[neutralized]"

# Refusal texts. Constants so the tests assert on the module's own words rather
# than on a copy, and so the reason a dump was refused is stable enough for a
# handler to branch on.
DOCTYPE_REFUSAL = (
    "This screen's uiautomator dump declares a DOCTYPE or an ENTITY and was "
    "discarded unparsed. A real Android dump never does; something on the "
    "device produced it, so it is treated as hostile rather than repaired."
)
OVERSIZE_REFUSAL = (
    "This screen's uiautomator dump is larger than the "
    + str(MAX_DUMP_BYTES)
    + " byte cap and was discarded before parsing."
)
NOT_XML_REFUSAL = (
    "This screen's uiautomator dump could not be parsed as XML and was "
    "discarded. Re-dump the screen; if it keeps failing, a secure window (a "
    "password field or a payment sheet) is blocking accessibility."
)


#: The roles a control can be given, IN PRIORITY ORDER, each with the whole
#: words that earn it. The first group that matches wins and the rest are not
#: consulted, so a control labelled "Send voice message" is a ``send``; an
#: element never carries two roles. ``input`` is not here because it is decided
#: by the ``editable`` flag rather than by a word.
#:
#: Deliberately SMALL and conservative. A role is a hint the model targets by,
#: and a wrong hint is worse than none: on 2026-09-04 the model tapped a Voice
#: mode control as Send, twice, which opened the microphone permission dialog
#: and pushed the app out to the launcher.
ROLE_LEXICON: tuple = (
    ("send", ("send", "submit", "enviar", "envoyer")),
    ("voice", ("voice", "record", "mic", "microphone", "audio")),
    ("back", ("back", "backward")),
    ("close", ("close", "dismiss")),
)

ROLE_INPUT = "input"

#: A password input. Ahead of ``input`` in the priority order, because what a
#: field TAKES matters more than that it takes typing at all: a plain ``type``
#: into one is refused, whatever the field is called and in whatever alphabet.
ROLE_PASSWORD = "password"

#: Packages whose presence in a dump MEANS a modal is up: the app under test is
#: still running underneath, so `back` dismisses this and the case continues.
#:
#: It lives HERE rather than in ``executor`` because the answer has to be taken
#: while the dump is still being read. A permission prompt is a CARD over a
#: full-screen app window, so it never wins the dominant-package question -- and
#: keying the dialog rule on that answer made it inert for exactly the packages
#: it exists for. Two questions, two fields.
#:
#: **THE MEMBERSHIP RULE, and it is the whole safety of this set: a package
#: belongs here only if it CANNOT appear in an ordinary screen's dump.**
#: `com.android.systemui`, `android` and `com.google.android.gms` were in here
#: for one day and are the counter-example -- systemui is the status bar and the
#: navigation bar, which are in EVERY dump, so once the rule read any element's
#: package every ordinary screen became a dialog, every first action halted the
#: replay, `back` changed nothing and every case burned its escapes and reported
#: blocked. That was strictly worse than the inert detection it was fixing.
#:
#: Catching those three needs a DIFFERENT detector -- a window-level one -- not a
#: bigger set, and adding one here without it will fail the test that pins this
#: rule rather than fail quietly on a tester's machine.
SYSTEM_DIALOG_PACKAGES: frozenset = frozenset(
    {
        "com.android.permissioncontroller",
        "com.google.android.permissioncontroller",
        "com.android.packageinstaller",
        "com.google.android.packageinstaller",
    }
)

#: How much of a wrapper its borrowed label must cover before the wrapper is
#: judged to BE that control. Measured, not guessed: on the 2026-09-04 chat
#: screen the Send wrapper is 144x144 and its label child 96x96 -- 44%. The
#: root FrameLayout of the same screen contains that child too, at a fraction
#: of a percent, and borrowing there gave the whole screen the name of whatever
#: small label happened to be smallest. A share plus the clickable test is what
#: separates "a control with its label inside it" from "a container with things
#: in it".
MIN_LABEL_AREA_SHARE = 0.25

_CAMEL_LOWER_UPPER = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_CAMEL_UPPER_WORD = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_WORD_RE = re.compile(r"[a-z0-9]+")


def split_camel(text: object) -> str:
    """``DeleteAccountButton`` -> ``Delete Account Button``.

    Lives here, and not in ``executor``, because ``perception`` is the module
    ``executor`` already imports -- the other direction would be a cycle. Having
    ONE tokenising idiom in this package is the point: the destructive guard,
    the role lexicon and the credential mask all ask the same question of a
    string, and three private copies of the answer is how they drift.
    """
    if not text:
        return ""
    return _CAMEL_UPPER_WORD.sub(" ", _CAMEL_LOWER_UPPER.sub(" ", str(text)))


def words(*values: object) -> list:
    """Every lowercase word in *values*, camel-case runs split first."""
    out: list = []
    for value in values:
        out.extend(_WORD_RE.findall(split_camel(value).lower()))
    return out


#: Anything alphabetic that :func:`words` cannot see. The tokeniser is ASCII on
#: purpose -- the role lexicon and the destructive lexicon are English words an
#: Android build emits -- but that makes it BLIND rather than permissive, and a
#: caller that treats "no token matched" as "nothing to worry about" has built a
#: security control that cannot fail closed.
_UNREADABLE_RE = re.compile(r"[^\x00-\x7f]")


def has_unreadable_text(*values: object) -> bool:
    """True when any of *values* holds a character :func:`words` cannot tokenise.

    The distinction this exists to draw: "this name matched nothing in my list"
    and "this name is not in an alphabet I can read" are different answers, and
    only the first is evidence. A caller deciding whether to PRINT something
    must treat the second as a refusal.
    """
    for value in values:
        if _UNREADABLE_RE.search(str(value or "")):
            return True
    return False


def _own_label(element: object) -> str:
    """What this element says about ITSELF. ``content-desc`` first.

    ``desc`` beats ``text`` because an icon-only control has only a desc, and
    where both exist the desc is the accessible name -- the thing a tester would
    call the control.
    """
    if not isinstance(element, dict):
        return ""
    desc = str(element.get("desc") or "").strip()
    return desc or str(element.get("text") or "").strip()


def _bounds_tuple(element: object) -> tuple | None:
    box = (element or {}).get("bounds") if isinstance(element, dict) else None
    if not (isinstance(box, (list, tuple)) and len(box) == 4):
        return None
    try:
        return tuple(int(value) for value in box)
    except (TypeError, ValueError, OverflowError):
        return None


def label_of(element: object, elements: object) -> str:
    """The name a tester would give *element*, borrowing a child's if it has none.

    The clickable thing and the labelled thing are OFTEN different nodes: on the
    2026-09-04 screen the Send affordance is a clickable ``View`` with empty
    text and desc, wrapping a non-clickable child whose desc is "Send". Three
    controls on that screen looked identical in the packet for exactly this
    reason.

    Only an element with no label of its own borrows one, only if it is itself
    TAPPABLE, and only from a CONTAINED element whose own area covers at least
    :data:`MIN_LABEL_AREA_SHARE` of it. The lower bound is load-bearing:
    without it the root FrameLayout of the fixture -- which contains every word
    on the screen -- took the label of whichever contained element was
    smallest, and the packet then named the whole screen "Send".

    CONTAINED, NOT STRICTLY SMALLER. An equal-bounds child counts, and used not
    to. ``match_parent`` x ``match_parent`` is the commonest button shape on
    Android -- a clickable wrapper with an empty label around a TextView filling
    it exactly -- so excluding equal area excluded the ordinary case, and the
    control reached the packet unlabelled while the tester was looking straight
    at its name.

    There is no upper AREA bound, because there cannot be one that does
    anything: the containment test above already requires the child to lie
    inside this element, and a box inside another box cannot have the larger
    area. The rule carried ``area >= own_area`` for exactly one reachable case
    -- equal area -- and once that became legitimate the clause was dead. A
    mutation removing it survived, which is how it was found: an unreachable
    condition reads as a safeguard and grades as nothing. What actually bounds
    this from above is CONTAINMENT, and that is where the test aims.

    This rule and the destructive guard's (``executor.contained_texts``) read
    the same geometry for different purposes, and they DIVERGED here: the guard
    was fixed to include equal area after a transfer CTA tapped through
    unguarded, and this mirror of it was not. No safety gap -- the guard reads
    containment itself and never consults this -- but the packet the model
    plans from disagreed with the packet the guard judged. Pinned by
    ``tests/mobile/test_mobile_label_containment.py``, which asserts the two
    rules agree rather than asserting each one separately.
    """
    own = _own_label(element)
    if own:
        return own
    if not (isinstance(element, dict) and element.get("clickable")):
        return ""
    outer = _bounds_tuple(element)
    if outer is None:
        return ""
    x1, y1, x2, y2 = outer
    own_area = max(0, x2 - x1) * max(0, y2 - y1)
    best = ""
    best_area = None
    for other in list(elements or []):
        if not isinstance(other, dict) or other is element:
            continue
        inner = _bounds_tuple(other)
        if inner is None:
            continue
        a1, b1, a2, b2 = inner
        if a1 < x1 or b1 < y1 or a2 > x2 or b2 > y2:
            continue
        area = max(0, a2 - a1) * max(0, b2 - b1)
        if area < own_area * MIN_LABEL_AREA_SHARE:
            continue
        text = _own_label(other)
        if not text:
            continue
        # The LARGEST qualifying child, not the smallest: among the children
        # that fill enough of this control to be its label, the biggest is the
        # one the control is drawn around.
        if best_area is None or area > best_area:
            best, best_area = text, area
    return best


def role_of(element: object, label: str = "") -> str:
    """``input``/``send``/``voice``/``back``/``close``, or ``""``. Never guesses.

    Matched as WHOLE WORDS over the label and the resource-id, so ``Compass``
    is not a ``send`` and ``Recording saved`` is a ``voice`` only because
    ``record``'s own word is not in it -- ``recording`` is a different token.
    That strictness is deliberate: a wrong role is worse than none.
    """
    if not isinstance(element, dict):
        return ""
    if element.get("secure"):
        return ROLE_PASSWORD
    if element.get("editable"):
        return ROLE_INPUT
    rid = str(element.get("rid") or "").rsplit("/", 1)[-1]
    tokens = set(words(label or _own_label(element), rid))
    if not tokens:
        return ""
    for role, terms in ROLE_LEXICON:
        if tokens.intersection(terms):
            return role
    return ""


def annotate(content: object) -> object:
    """Give every element in a pruned screen its ``label`` and its ``role``.

    Separated from :func:`prune` so it can be exercised against a screen read
    back from a run's own ``screens/`` directory -- which is how the 2026-09-04
    chat screen became a fixture in this repository rather than an anecdote.
    Idempotent.
    """
    body = content if isinstance(content, dict) else {}
    elements = [e for e in (body.get("elements") or []) if isinstance(e, dict)]
    for element in elements:
        label = label_of(element, elements)
        element["label"] = label
        element["role"] = role_of(element, label)
    return content


def _clean(value: object) -> str:
    """One untrusted attribute value, made safe to show and to store."""
    text = value if isinstance(value, str) else str(value or "")
    text = _CONTROL_RE.sub("", text).replace("\r", " ").replace("\n", " ")
    for marker in GUARD_MARKERS:
        if marker.lower() in text.lower():
            text = re.sub(re.escape(marker), NEUTRALIZED, text, flags=re.IGNORECASE)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > MAX_ATTR_CHARS:
        text = text[:MAX_ATTR_CHARS] + "..."
    return text


def _flag(node: ElementTree.Element, name: str) -> bool:
    return str(node.get(name) or "").strip().lower() == "true"


def parse_bounds(raw: object) -> tuple[int, int, int, int] | None:
    """``[x1,y1][x2,y2]`` -> a 4-tuple, or None when it is not that shape."""
    match = _BOUNDS_RE.search(str(raw or ""))
    if not match:
        return None
    x1, y1, x2, y2 = (int(match.group(index)) for index in (1, 2, 3, 4))
    return x1, y1, x2, y2


def _area(bounds: tuple[int, int, int, int]) -> int:
    return max(0, bounds[2] - bounds[0]) * max(0, bounds[3] - bounds[1])


def _short_class(full: str) -> str:
    tail = str(full or "").rsplit(".", 1)[-1]
    return tail[:60]


def _is_editable(full_class: str) -> bool:
    text = str(full_class or "")
    return any(hint in text for hint in EDITABLE_CLASS_HINTS)


def _display_rect(tree: object, display: object, rotation: object) -> tuple:
    """``(display, frame, axes)`` -- the screen, the region an element may
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
    right = 0
    bottom = 0
    panel_right = 0
    panel_bottom = 0
    try:
        laid_out = list(tree.iter("node"))
        windows = [child for child in tree if getattr(child, "tag", None) == "node"]
    except (TypeError, AttributeError):
        laid_out = []
        windows = []
    for node in laid_out:
        # EVERY node for the FRAME. "At least as large as everything laid out on
        # it" is the promise, and a child laid out beyond its own window is
        # still laid out.
        bounds = parse_bounds(node.get("bounds"))
        if bounds is None or _area(bounds) <= 0:
            continue
        right = max(right, bounds[2])
        bottom = max(bottom, bounds[3])
    for node in windows:
        # The depth-1 WINDOWS for the panel estimate, which is a different
        # question with a different consumer. A window is a display-sized thing
        # and a child is not, so when the device could not tell us the display,
        # the union of the windows is the closest honest guess at the panel --
        # and an overhanging row must NOT be allowed to define it. Actions clamp
        # to this rectangle: `executor` will not swipe outside the panel, and a
        # row dragged out to its own far edge would silently turn that clamp
        # into a no-op and send a gesture the device ignores.
        bounds = parse_bounds(node.get("bounds"))
        if bounds is None or _area(bounds) <= 0:
            continue
        panel_right = max(panel_right, bounds[2])
        panel_bottom = max(panel_bottom, bounds[3])

    size = None
    if isinstance(display, (list, tuple)) and len(display) == 2:
        numbers = []
        for value in display:
            if isinstance(value, bool):
                numbers = []
                break
            try:
                numbers.append(int(value))
            except (TypeError, ValueError, OverflowError):
                numbers = []
                break
        # `numbers` rather than `len(numbers) == 2`: the arity is decided ONCE,
        # by the `len(display) == 2` above. Checking it twice meant neither
        # check was graded -- a mutant dropping either one was caught by the
        # other and SURVIVED, which reads as a verification gap and is really a
        # second layer doing the first layer's job.
        if numbers and all(0 < n <= MAX_DISPLAY_EXTENT for n in numbers):
            size = numbers

    if size is not None:
        try:
            # `in (1, 3)`, NOT `% 4 in (1, 3)`. A rotation is 0..3; anything else
            # is not a rotation this dump can have and must be treated as no
            # turn. The modulo was worse than useless -- it made `-1` and a
            # twenty-digit number into a quarter turn, so the frame's axes
            # swapped on garbage. A value out of range is one we cannot read --
            # and because an unreadable rotation would otherwise leave the frame
            # narrower than a landscape dump, the widening below is what stops
            # that being a lost screen too.
            turned = int(rotation) in (1, 3)
        except (TypeError, ValueError, OverflowError):
            turned = False
        width, height = (size[1], size[0]) if turned else (size[0], size[1])
    else:
        # The window walk's answer and NOTHING ELSE -- never `or right`, which
        # handed the panel the FRAME's rule (the all-node extent) whenever a
        # depth-1 window was zero-area, carried unparseable bounds, or carried
        # no bounds at all. One overhanging row then declared the screen 3000px
        # wide on a 1080 panel, which is the exact incident the two walks exist
        # to prevent. A fallback may not substitute the answer of a DIFFERENT
        # rule.
        #
        # There is deliberately NO test here on whether the walk answered. One
        # decision below settles whether this is a panel at all; asking in both
        # places made neither question gradable, because a mutant loosening
        # either was caught by the other and reported as a survivor.
        #
        # THE COST, because it is not free and it is not only the pathological
        # dump: where such a window's children happen to lie INSIDE the screen,
        # the extent was the right answer by luck, and that luck is now
        # declined. The report falls to the stored frame for its scale and the
        # executor's far swipe clamps stop binding (its documented path for an
        # absent display). Both are worse than a correct panel and better than a
        # confidently wrong one, and neither is reached at all when the device
        # answers `wm size` -- which it did on every device this has been run
        # against.
        width, height = panel_right, panel_bottom

    # The FRAME is derived whether or not the panel is known, and is returned
    # even when the panel is not. `prune` takes the first positive-area node as
    # its visibility filter when the frame is None, and that node being mistaken
    # for the screen is the round-1 defect -- a status bar became the display and
    # everything below it was discarded. So `(None, None)` means one thing only:
    # no positive-area node has a positive right OR a positive bottom edge. That
    # is NOT "the dump laid out nothing" -- a node at `[-200,-100][-100,0]` has
    # positive area and reaches the packet while both rectangles are empty --
    # and the looser claim was in three docstrings.
    frame_right, frame_bottom = max(width, right), max(height, bottom)
    if frame_right <= 0 or frame_bottom <= 0:
        # Three values on EVERY path. Nothing is known here -- no panel, no
        # frame, and neither axis -- and `(0, 0)` is how that is said in the
        # per-axis shape.
        return None, None, (0, 0)
    # THE ONE PLACE THE PANEL IS DECIDED. Both axes or neither: a rectangle with
    # one axis from the windows and the other from every node is one neither
    # rule would have chosen, and the dump that produces it is a window with
    # positive AREA whose right edge is not positive -- laid out entirely at
    # negative x, so it contributes a bottom and no right.
    panel = (0, 0, width, height) if width > 0 and height > 0 else None
    # PER AXIS, decided in the same breath as the rectangle so the two cannot
    # drift. A rectangle cannot say "width known, height unknown", and that is
    # the whole cause of two rounds of defects: round 4 filled a missing axis
    # from the FRAME and let an overhanging child declare the screen 3000px
    # wide; round 7 refused to fill it and threw away the axis this walk HAD
    # answered, so `root_bounds` went empty, the swipe clamp had nothing to
    # clamp to, and the destructive guard judged a node with no strings instead
    # of the control under the finger. Publishing what is actually known, per
    # axis, is the only answer that is neither.
    #
    # No `max(0, ...)` clamp here: both branches above leave these at zero or
    # more, so it would be an unreachable condition -- which reads as a
    # safeguard and grades as nothing, the defect `ed7b1ecf` deleted from
    # `label_of` in this same file.
    return panel, (0, 0, frame_right, frame_bottom), (width, height)


def _dominant_package(elements: object) -> str:
    """Whose screen this is: the package of the element covering the most of it.

    NOT the first node's, which is what it used to be. A uiautomator dump holds
    every window, and when a system overlay sorted first the whole screen was
    renamed after it -- so the left-the-app check fired on every settle, told
    the model to `launch` (which changed nothing, because the next dump looked
    identical), and spent all three of the case's escapes on an unhelpful
    recovery.

    Area is the right discriminator because an overlay is, by definition, drawn
    over part of a window that is larger than it. A tie keeps the earlier
    element, so the answer is stable for a screen that really is one window.
    """
    best = ""
    best_area = -1
    for element in list(elements or []):
        if not isinstance(element, dict):
            continue
        name = str(element.get("package") or "")
        if not name:
            continue
        bounds = element.get("bounds") or []
        if len(bounds) != 4:
            continue
        try:
            x1, y1, x2, y2 = (int(value) for value in bounds)
        except (TypeError, ValueError, OverflowError):
            continue
        area = max(0, x2 - x1) * max(0, y2 - y1)
        if area > best_area:
            best, best_area = name, area
    return best


def _screen_id(package: str, activity: str, texts: list[str]) -> str:
    """Stable identity of a screen: package + activity + its top three texts.

    Deliberately NOT the full element hash -- a list that scrolled by one pixel
    is the same screen to a tester, and the report dedupes on this.
    """
    seed = "|".join([package, activity] + texts[:3])
    return hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[:12]


#: How many hex characters of an element's own content hash become its id.
#: 32 bits over at most MAX_ELEMENTS elements; a collision INSIDE one dump is
#: handled explicitly by ``_assign_ids`` rather than left to luck.
ID_HASH_CHARS = 8

#: Kept short because the id travels in every packet and back in every target,
#: and ``actions.Target.id`` caps the field at 16 characters.
ID_PREFIX = "e"


def element_seed(element: object) -> str:
    """The identity of ONE pruned element, as text.

    This is the seed ``prune`` already hashed for the screen's ``hash`` field,
    factored out rather than duplicated: the SCREEN hash and the ELEMENT id are
    now computed from the same five observables, so the two can never drift
    into disagreeing about what "the same element" means.
    """
    body = element if isinstance(element, dict) else {}
    bounds = body.get("bounds") or []
    return "|".join(
        [
            str(body.get("cls") or ""),
            str(body.get("text") or ""),
            str(body.get("desc") or ""),
            str(body.get("rid") or ""),
            ",".join(str(value) for value in bounds),
        ]
    )


def element_id(element: object) -> str:
    """The CONTENT-derived id of one pruned element.

    Ids used to be row numbers (``"e" + str(index)``), and that was the defect
    behind a tester watching the lane tap the microphone instead of send. A
    ``type`` action is in ``actions.MUTATING_OPS``, so the executor re-dumps and
    REPLACES the screen after it; a chat app then grows a send control once the
    field is non-empty, every row at or after that slot shifts by one, and the
    tap planned as the second element resolved -- confidently, with ``how="id"``
    and ``candidates=1`` -- to its neighbour. ``MAX_ELEMENTS`` truncation
    shifted them again.

    A content-derived id makes that class unrepresentable BY CONSTRUCTION
    rather than by a check somebody has to remember: the id IS a function of
    the element's own five observables, so an id minted against dump N can only
    match an element with the same class, text, description, resource id and
    bounds on dump N+1 -- the same element by everything the model was shown.
    When the element really changed, the id stops matching and the executor's
    existing boomerang hands the CURRENT screen back to be re-planned, which is
    the honest answer rather than a wrong tap.
    """
    digest = hashlib.sha256(
        element_seed(element).encode("utf-8", errors="replace")
    ).hexdigest()
    return ID_PREFIX + digest[:ID_HASH_CHARS]


def _assign_ids(elements: list) -> None:
    """Stamp every element with its content id, in place, uniquely.

    Two nodes CAN share all five observables -- a wrapper and its only child
    routinely share bounds and text -- and two elements under one id would put
    ``actions.resolve_target`` straight back to guessing. A collision therefore
    takes a deterministic ordinal suffix in dump order, so the id is still
    stable for a given dump and still never addresses a different content.
    """
    seen: dict[str, int] = {}
    for element in elements:
        base = element_id(element)
        count = seen.get(base, 0) + 1
        seen[base] = count
        element["id"] = base if count == 1 else base + "-" + str(count)


def prune(
    xml: object,
    activity: str = "",
    display: object = None,
    density: object = None,
) -> dict:
    """Untrusted dump -> ``{"screen_id", "elements", "hash", "package", ...}``.

    Returns the ``{"error", "content"}`` shape every module in this package
    returns, rather than the bare dict the programme spec sketched: a refusal
    here (hostile dump, oversize, unparseable) is information a handler must
    render, and a bare dict has nowhere to put it.
    """
    try:
        if not isinstance(xml, str) or not xml.strip():
            return {"error": NOT_XML_REFUSAL, "content": None}
        if len(xml.encode("utf-8", errors="replace")) > MAX_DUMP_BYTES:
            return {"error": OVERSIZE_REFUSAL, "content": None}
        if _DECL_RE.search(xml):
            return {"error": DOCTYPE_REFUSAL, "content": None}
        try:
            root = ElementTree.fromstring(xml)
        except ElementTree.ParseError:
            return {"error": NOT_XML_REFUSAL, "content": None}

        # The DISPLAY -- from the device where the caller had it, and from the
        # extent of the dump's own windows where it did not. Never inferred from
        # which window "looks like" the display: that question has no answer in
        # a dump, and four rounds of asking it are in docs/DECISIONS.md.
        # TWO rectangles, because the two readers fail in opposite directions.
        # `selected` is the display and becomes the report's scale; `frame` is
        # the display widened to cover everything the dump lays out, and is the
        # visibility filter, because a filter smaller than the dump deletes the
        # tester's screen in silence. See `_display_rect`.
        selected, frame, panel_axes = _display_rect(root, display, root.get("rotation"))
        root_bounds = frame
        elements: list[dict] = []
        texts: list[str] = []
        package = ""
        considered = 0
        for node in root.iter("node"):
            considered += 1
            bounds = parse_bounds(node.get("bounds"))
            if bounds is None or _area(bounds) <= 0:
                continue
            if root_bounds is None:
                root_bounds = bounds
            elif (
                bounds[0] >= root_bounds[2]
                or bounds[1] >= root_bounds[3]
                or bounds[2] <= root_bounds[0]
                or bounds[3] <= root_bounds[1]
            ):
                # Entirely off-screen: laid out but not visible to the tester.
                continue

            full_class = str(node.get("class") or "")
            text = _clean(node.get("text"))
            desc = _clean(node.get("content-desc"))
            rid = _clean(node.get("resource-id"))
            clickable = _flag(node, "clickable") or _flag(node, "long-clickable")
            scrollable = _flag(node, "scrollable")
            editable = _is_editable(full_class)
            secure = _flag(node, "password")
            if not (text or desc or rid or clickable or scrollable or editable):
                # Pure layout scaffolding: not a target, not assertable.
                continue

            package = package or _clean(node.get("package"))
            if text:
                texts.append(text)
            elements.append(
                {
                    "id": "",
                    "cls": _short_class(full_class),
                    "text": text,
                    "desc": desc,
                    "rid": rid,
                    "bounds": list(bounds),
                    "clickable": clickable,
                    "editable": editable,
                    # A password input, from the dump's own attribute. It is
                    # the only signal that survives a field named in an
                    # alphabet this server cannot read, which is why the
                    # credential rule leans on the ELEMENT here and not on the
                    # action's chosen names alone.
                    "secure": secure,
                    "checked": _flag(node, "checked"),
                    "scrollable": scrollable,
                    # Kept per element only so the root-package fallback above
                    # has something to fall back TO; stripped before the packet
                    # is rendered, because it is the same on every element.
                    "package": _clean(node.get("package")),
                }
            )

        # OFF-PANEL CROWDING MAY NEVER DELETE AN ON-PANEL CONTROL.
        #
        # The frame above is the display WIDENED to cover everything the dump
        # lays out, which is what stops a stale device size deleting the
        # tester's screen. But widening admits off-display elements into the same
        # `MAX_ELEMENTS` budget in DOCUMENT order, so an adjacent pager page or
        # an incoming activity laid out one screen width away could fill the cap
        # and evict the control the tester is looking at. Measured: 150 rows of
        # an incoming activity listed first, and `Delete account` gone from the
        # packet, with a CORRECT display.
        #
        # Promoting what intersects the panel fixed that and broke its mirror
        # image. Measured the other way: a tablet dump under a STALE phone size
        # -- the case the widening above exists for -- classified `Delete
        # account` at document index 0 as off-display and evicted it, while both
        # the correct display and the dump-derived fallback kept it.
        #
        # The two cannot be told apart from here. A panel narrower than the dump
        # extent is either a stale panel (keep the extra) or an app drawing
        # off-screen (drop it), and the geometry is identical in kind -- rounds 3
        # and 4 already made that trade in both directions.
        #
        # FOUR passes at spending one budget between the two classes each fixed
        # the previous pass's worst case and created a new one further out:
        # document order lost the visible control behind a full cap of off-panel
        # rows; promotion lost the control a STALE panel misclassified;
        # reserving half the budget for document order lost 25 visible controls
        # behind 75 off-panel ones; reinstating that reserve lost 35 at 130.
        # Each was measured, and the pattern is the point -- at a fixed cap,
        # "every on-panel element survives" and "every element the dump listed
        # first survives" are JOINTLY UNSATISFIABLE. That is a counting fact and
        # no ordering rule repeals it.
        #
        # So the fix leaves the ordering axis: the packet's TOTAL grew by
        # :data:`MAX_PACKET_HEADROOM`, the inside class keeps its own cap, the
        # outside class takes what the total leaves, and the walk preserves
        # DOCUMENT order. The headroom is spent only when the dump disagrees
        # with the panel, so an ordinary screen is capped exactly as it was.
        #
        # When something IS dropped, `dropped_inside_panel` /
        # `dropped_outside_panel` say which side of the panel it was on, because
        # a screen thinned without saying so is one the model plans against
        # believing it is complete.
        #
        # The properties that hold over ANY dump, which are the ones worth
        # grading: off-panel crowding never removes an on-panel element, and no
        # dump loses more elements than it loses without any of this.
        # UNCHANGED MEANING, deliberately: "the dump offered more than
        # `MAX_ELEMENTS`". `screen_hit` refuses a press on a truncated packet --
        # a CLAUDE.md hard rule, because the control a key reaches is not in the
        # dump at all -- so redefining this flag as "something was dropped"
        # would relax a destructive guard as a side effect of a budget change,
        # on a lane that reaches real installs. The new counts below carry the
        # better information without touching what this gates.
        truncated = len(elements) > MAX_ELEMENTS
        dropped_inside = 0
        dropped_outside = 0
        dropped_unclassified = 0
        if selected is not None:
            # Classified ONCE, into a list, because the same visibility test
            # written twice is two conditions that drift apart -- and the box is
            # bound once per element for the same reason.
            classified = []
            for element in elements:
                box = element.get("bounds") or []
                classified.append(
                    (
                        element,
                        len(box) == 4
                        and not (
                            box[0] >= selected[2]
                            or box[1] >= selected[3]
                            or box[2] <= selected[0]
                            or box[3] <= selected[1]
                        ),
                    )
                )
            inside_count = sum(1 for _, visible in classified if visible)
            # THE TOTAL IS BOUNDED; THE OUTSIDE CLASS IS NOT CAPPED.
            #
            # Outside elements get whatever the total leaves after the inside
            # class has taken its own cap. Capping the outside class instead
            # deleted content from dumps that FIT -- 110 elements on a tablet
            # under a stale panel lost 25 of the tester's controls, where
            # today's code carries all 110 -- and it deleted them from the one
            # class whose membership we have already decided we cannot trust.
            #
            # `min(inside_count, MAX_ELEMENTS)` IS THE INVARIANT. Delete the
            # clamp and this term keeps growing with the inside class, so inside
            # overflow starts eating the outside budget and the two classes are
            # competing again -- which is the defect all four ordering rules
            # died of. With the clamp, the budget is pinned at the headroom once
            # the inside class is full, so inside overflow adds NOTHING to
            # outside loss.
            outside_budget = (
                MAX_ELEMENTS + MAX_PACKET_HEADROOM - min(inside_count, MAX_ELEMENTS)
            )
            kept: list[dict] = []
            used_inside = 0
            used_outside = 0
            for element, visible in classified:
                # DOCUMENT ORDER, and the independence is ONE-DIRECTIONAL: an
                # inside decision reads only the inside counter and its own cap,
                # so nothing the outside class does can evict an inside element.
                # The reverse is not true and does not need to be -- the outside
                # budget is a function of `inside_count` -- and the clamp above
                # is what stops that dependence turning back into competition.
                if visible:
                    if used_inside >= MAX_ELEMENTS:
                        dropped_inside += 1
                        continue
                    used_inside += 1
                else:
                    if used_outside >= outside_budget:
                        dropped_outside += 1
                        continue
                    used_outside += 1
                kept.append(element)
            elements = kept
        else:
            # No panel, so no classification to make: one budget, document
            # order, and the drops are counted under their own name. Attributing
            # them to the inside class would be inventing a classification from
            # a rectangle we do not have, which is the mistake this function's
            # whole history is made of.
            dropped_unclassified = max(0, len(elements) - MAX_ELEMENTS)
            elements = elements[:MAX_ELEMENTS]
        _assign_ids(elements)
        # AFTER the cap and the ids, so a label is only ever borrowed from an
        # element the model can actually see and name. It reads cls/text/desc
        # and writes label/role, so the five observables the ids are derived
        # from are already final when it runs.
        annotate({"elements": elements})

        # The SCREEN hash and every ELEMENT id come from the same per-element
        # seed, so "did this screen change" and "is this the same element"
        # cannot answer from two different notions of identity.
        seed = "\x1f".join(element_seed(element) for element in elements)
        package = _dominant_package(elements) or package
        # ANY element's, not the dominant one's: an overlay is smaller than the
        # window it covers by definition, so these are two different questions
        # about one dump and each needs its own answer.
        dialog = ""
        for element in elements:
            name = str(element.get("package") or "")
            if not dialog and name in SYSTEM_DIALOG_PACKAGES and name != package:
                dialog = name
        for element in elements:
            element.pop("package", None)
        content = {
            "screen_id": _screen_id(package, _clean(activity), texts),
            "elements": elements,
            "hash": hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[
                :16
            ],
            "package": package,
            "dialog_package": dialog,
            "activity": _clean(activity),
            # The DISPLAY. Stored because the tallest element bottom is NOT
            # the viewport: a scroll container reports its CONTENT height, and
            # deriving the device size from a max() walk shrank every element on
            # every scrollable screen. `[]` when the dump carried no positive-area
            # window at all, so the key means exactly one thing and a reader can
            # treat an empty value and an older build's missing value alike.
            "root_bounds": list(selected) if selected else [],
            # The FRAME, under its OWN name because it answers a different
            # question: `root_bounds` is the display, and this is the region an
            # element may occupy -- at least as large as everything the dump
            # lays out. Stored because the report needs a scale even when the
            # panel is unknown, and the alternative was `report._device_size`
            # improvising one from a max() walk over the elements, which returns
            # a scroll container's CONTENT height: the one value that whole
            # function documents as the thing the scale must never be. Two
            # rectangles, two names, one meaning each.
            #
            # From `frame`, NOT from the local `root_bounds` above -- that local
            # is reassigned to the first positive-area node when the frame is
            # None, so storing it would put a node's own box under a name that
            # promises the frame, which is the one-name-two-meanings defect this
            # key exists to end.
            "frame_bounds": list(frame) if frame else [],
            # WHICH class lost elements, because "truncated" alone cannot say
            # whether the tester's own screen was thinned or an adjacent pager
            # page was. A model told only that something was cut plans against a
            # screen it believes is complete.
            #
            # NAMED FOR THE PANEL, NOT FOR THE TESTER'S EYES, because the panel
            # can be wrong and these inherit its error. Under a stale display a
            # control the tester is looking at falls OUTSIDE the panel, so
            # `dropped_outside_panel` would have been read as "only an adjacent
            # page was thinned" when the opposite is true. "Inside"/"outside"
            # says what was actually measured -- which side of a rectangle -- and
            # `root_bounds == []` tells a reader that rectangle was never known.
            # A count named after what the tester can SEE would be presenting
            # the panel's guess as provenance, which is the confident wrongness
            # the rest of this function exists to avoid.
            "dropped_inside_panel": dropped_inside,
            "dropped_outside_panel": dropped_outside,
            # No panel at all: not attributable to either side.
            "dropped_unclassified": dropped_unclassified,
            # The panel PER AXIS, `0` for an axis its own rule could not answer.
            # `root_bounds` above is the same panel when BOTH axes are known and
            # `[]` otherwise, which is what the report needs -- a scale must be a
            # whole rectangle or absent. The gesture clamp needs the other shape:
            # it can bind one axis while leaving the other alone, and collapsing
            # that into the rectangle is what cost a real clamp on a window laid
            # out above the display. Two consumers, two values, one meaning each.
            "panel_axes": list(panel_axes),
            "truncated": truncated,
            "considered": considered,
        }
        # THE AUDIT, over the screen this function just produced and nothing
        # else. One producer: the packet, the report and the run's stored screen
        # all read this key rather than each deriving an answer of their own.
        # Imported HERE rather than at module scope because `screen_audit`
        # imports this module for its one tokeniser; by the time a prune runs,
        # this module is fully loaded, so the cycle cannot bite.
        from tools.mobile import screen_audit

        content["accessibility"] = screen_audit.audit(content, density)
        return {"error": None, "content": content}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.perception.prune failed")
        return {"error": str(exc), "content": None}


def element_line(element: dict, refs: dict | None = None) -> str:
    """One pruned element as a single compact line.

    ``refs`` maps a FIELD NAME to the id of the element that already printed
    that field's value, and only :func:`render_lines` passes it. With the
    default ``None`` this function is byte-for-byte what it always was, which is
    what lets the pins that grade it keep grading it.

    THE SUBSTITUTION HAPPENS ON THE PART, NEVER ON THE FINISHED LINE. Replacing
    ``desc="V"`` in the joined string finds the FIRST occurrence of that
    substring, and no attribute is escaped -- so an element whose *text* value
    happens to read ``desc="V"`` had its TEXT rewritten to ``desc=@e1`` while
    the duplicate desc was still printed in full. The model was handed a value
    the app never displayed, on exactly the kind of screen this feature targets:
    a chat carrying markup. Found by EXECUTING, in the adversarial review of the
    commit that introduced it.
    """
    pointers = refs if isinstance(refs, dict) else {}
    parts = [str(element.get("id") or "?"), str(element.get("cls") or "?")]
    text = str(element.get("text") or "")
    desc = str(element.get("desc") or "")
    rid = str(element.get("rid") or "")
    if text:
        parts.append(
            "text=@" + pointers["text"] if "text" in pointers else 'text="' + text + '"'
        )
    if desc and desc != text:
        parts.append(
            "desc=@" + pointers["desc"] if "desc" in pointers else 'desc="' + desc + '"'
        )
    if rid:
        parts.append("rid=" + rid)
    label = str(element.get("label") or "")
    if label and label not in (text, desc):
        # Only when it adds something: for a labelled control the label IS the
        # desc, and printing it twice spends the packet's budget on nothing.
        parts.append('label="' + label + '"')
    role = str(element.get("role") or "")
    if role:
        parts.append("role=" + role)
    bounds = element.get("bounds") or []
    if len(bounds) == 4:
        parts.append("at[" + ",".join(str(int(v)) for v in bounds) + "]")
    for name in ("clickable", "editable", "checked", "scrollable"):
        if element.get(name):
            parts.append(name)
    return " ".join(parts)


def _rows(elements: object) -> list[dict]:
    """The element list as dicts, or ``[]`` -- never an exception.

    ONE coercion, called by both consumers. ``list(elements or [])`` was written
    at each of them and raises TypeError on an int, and a stored packet is an
    outside input by the time it is read: the caller's ``except`` would discard
    the ENTIRE screen block over a malformed field, which is the shape this
    file's history is made of (see :func:`_count`). Two sites deriving one
    coercion is also the mirrored-condition defect CLAUDE.md names -- they
    drift, and the second one's fall-through is silent. Found by EXECUTING the
    parametrised malformed-input test, not by reading it.
    """
    if not isinstance(elements, (list, tuple)):
        return []
    return [element for element in elements if isinstance(element, dict)]


def render_lines(elements: object) -> list[str]:
    """Every element as a line, with each LONG repeated string printed once.

    :func:`element_line` dedupes ``text`` / ``desc`` / ``label`` WITHIN one node.
    It cannot see across the list, and a chat screen is where that costs: on the
    observed run mrun-20260905-051728-bd1777 (package
    an Arabic-language chat app) one 200-character Arabic reply was
    printed as a parent's ``label``, a child's ``desc``, a grandchild's ``text``
    and again in the controls summary -- four copies of one string per bubble,
    and every prior turn's bubbles persist, so the block grew with the length of
    the conversation.

    ``element_line`` is deliberately left ALONE and pure: it is a per-element
    function two test modules pin directly, and the cross-element pass is a
    different question about a different input. This function is the one
    producer of the rendered list; ``to_prompt_block`` and the display-invariant
    helpers both read it, so there is no second derivation to drift.

    The clauses, each graded by its own fixture in
    ``tests/mobile/screen_matrix.py``:

    * **The anchor is always EARLIER in this list than the reference.**
      ``to_prompt_block`` trims from the END, so a backward-only reference can
      never be orphaned by the trim. A dangling ``@e7`` is worse than the
      repetition it saved, and "recompute the dedupe after every trim" is the
      alternative that makes a quadratic loop cubic.
    * **``label=`` is never REPLACED, only ever used as an anchor.**
      ``actions.SELECTORS`` makes a label a targeting selector and the system
      prompt tells the model to prefer ``role`` or ``label``, so an abbreviated
      label costs a turn every time it is sent back. It still anchors, because
      the observed screen carried the sentence as a parent's label first and the
      child's ``desc`` has to be able to point at it.
    * **Short strings are left alone** (:data:`MIN_DEDUPE_CHARS`).

    Every element keeps its ``id``, which is what makes a referenced element
    still targetable rather than merely still described.
    """
    rows = _rows(elements)
    anchors: dict = {}
    out: list = []
    for element in rows:
        plain = element_line(element)
        eid = str(element.get("id") or "")
        refs: dict = {}
        for field in ("text", "desc"):
            value = str(element.get(field) or "")
            if len(value) < MIN_DEDUPE_CHARS:
                continue
            if (field + '="' + value + '"') not in plain:
                # Already suppressed by the within-node rule, or truncated to
                # something this element does not literally print. Either way
                # there is nothing here to replace, and replacing blind is how a
                # reference ends up pointing at a string nobody printed.
                continue
            seen = anchors.get(value)
            if seen and seen != eid:
                # Recorded as a FIELD to point at, and rendered by
                # `element_line` on the part itself. The previous version did a
                # `str.replace` on this finished line and could rewrite an
                # occurrence sitting inside a DIFFERENT attribute's value.
                refs[field] = seen
            elif eid:
                anchors.setdefault(value, eid)
        label = str(element.get("label") or "")
        if (
            len(label) >= MIN_DEDUPE_CHARS
            and eid
            and ('label="' + label + '"') in plain
        ):
            anchors.setdefault(label, eid)
        out.append(element_line(element, refs) if refs else plain)
    return out


def _eid(element: object) -> str:
    """The id string the summaries PRINT. ONE expression, every caller.

    For DISPLAY only. It is emphatically not a key: `prune` guarantees unique
    non-empty ids but a STORED packet does not, and two controls with no id
    collide on one entry -- which is how both of them came to read ``Voice
    mode``, the exact incident these summaries exist to prevent. Per-element
    values are keyed by object identity instead; see :func:`_summary_labels`.
    """
    body = element if isinstance(element, dict) else {}
    return str(body.get("id") or "")


def _summary_labels(elements: object) -> dict:
    """``id -> the label the summaries print``: abbreviated, never AMBIGUOUS.

    The ``fields`` and ``controls`` summaries exist because a model read three
    identical unlabelled Views and tapped Voice mode as Send. Nothing here may
    make two controls read the same, so the abbreviation is applied first and
    then any group whose SHORTENED labels collide while their FULL labels differ
    is restored to full length -- all of the group, not the shorter of it,
    because a reader comparing an abbreviation against a full string cannot tell
    which is which.

    Controls that genuinely share a label are left sharing it. That is the
    screen's own ambiguity, and hiding it would be the lie this pair of
    summaries was added to stop telling.

    KEYED THROUGH :func:`_eid`, which its two consumers also call. The first
    version wrote `str(id or "")` here and read `str(id)` there, so an element
    with `id` absent or `None` was stored under `""` and looked up under
    `"None"`: the lookup missed, the fallback was `""`, and the summary printed
    two controls as `None ` and `None ` -- or, with two empty ids, both as
    `Voice mode`. That is the incident these summaries exist to prevent,
    reintroduced by deriving one key in two places. Found by EXECUTING.
    """
    rows = _rows(elements)
    full = {}
    short = {}
    for element in rows:
        # KEYED BY OBJECT IDENTITY, not by the element's id string. `prune`
        # assigns unique non-empty ids, but a STORED packet is an outside input
        # and this module already ships a fixture whose first element has none.
        # Keyed by id string, two such controls shared one entry and the last
        # one won: both printed `Voice mode`, which is the incident the fields
        # and controls summaries exist to prevent. Found by EXECUTING.
        key = id(element)
        label = str(element.get("label") or "")
        full[key] = label
        short[key] = (
            label[:MAX_SUMMARY_LABEL_CHARS] + "\u2026"
            if len(label) > MAX_SUMMARY_LABEL_CHARS
            else label
        )
    groups: dict = {}
    for key, value in short.items():
        groups.setdefault(value, []).append(key)
    for keys in groups.values():
        if len(keys) > 1 and len({full[key] for key in keys}) > 1:
            for key in keys:
                short[key] = full[key]
    return short


def _count(value: object) -> int:
    """A drop count from a packet, or 0 -- never an exception.

    A stored packet is an outside input by the time it is read, and a bare
    ``int()`` on a field an older build never wrote (or a hostile one wrote as a
    string) would discard the ENTIRE screen block through the caller's
    `except`. Losing the screen to a malformed count is the shape this whole
    file's history is made of.
    """
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return number if number > 0 else 0


def to_prompt_block(pruned: object) -> str:
    """The pruned screen, wrapped for a prompt. ``""`` when there is nothing.

    The RAW XML never reaches this function's output: only the fields ``prune``
    produced, each already neutralised. That is what makes the packet compact
    and what a Phase-3 test asserts by looking for ``<node`` in a packet.
    """
    try:
        content = pruned if isinstance(pruned, dict) else {}
        if content.get("content") and isinstance(content.get("content"), dict):
            content = content["content"]
        # COERCED HERE, at the top, because this is the function whose `except`
        # discards the screen. `_rows` was first called only inside
        # `render_lines`, which sits BELOW the `fields` / `controls`
        # comprehensions -- so a malformed element list still raised on
        # `element.get` before the coercion was ever reached, and the tester's
        # model got an empty block for a reason nothing named. The test that was
        # supposed to grade this drove `render_lines` directly and never saw it.
        # Measure at the transport, not at the function.
        elements = _rows(content.get("elements"))
        if not elements:
            return ""
        header = [
            "screen " + str(content.get("screen_id") or "?"),
            "package " + str(content.get("package") or "?"),
        ]
        activity = str(content.get("activity") or "")
        if activity:
            header.append("activity " + activity)
        lines = [" | ".join(header)]
        # The two summaries a chat screen needed and did not have: which element
        # takes typing, and which controls can be tapped and what each is
        # called. Without them the model read three identical unlabelled Views
        # and tapped Voice mode as Send.
        fields = [element for element in elements if element.get("editable")]
        controls = [
            element
            for element in elements
            if element.get("clickable") and element.get("label")
        ]
        # Computed over ALL elements, not per summary: a field and a control
        # whose abbreviations collide are as confusing as two controls, and the
        # stricter input costs nothing.
        summary_labels = _summary_labels(elements)
        if fields:
            lines.append(
                "fields (type into these): "
                + "; ".join(
                    _eid(element)
                    + " "
                    + (
                        summary_labels.get(id(element))
                        or str(element.get("label") or "")
                        or "(unlabelled)"
                    )
                    for element in fields[:10]
                )
            )
        if controls:
            lines.append(
                "controls (tap these): "
                + "; ".join(
                    _eid(element)
                    + " "
                    + (
                        summary_labels.get(id(element))
                        or str(element.get("label") or "")
                    )
                    + (
                        " [" + str(element.get("role")) + "]"
                        if element.get("role")
                        else ""
                    )
                    for element in controls[:20]
                )
            )
        # BEFORE the element lines, because `wrap_untrusted` caps the block at
        # :data:`MAX_BLOCK_CHARS` and a 225-element packet runs past it -- so a
        # notice appended at the END was cut off on exactly the packets that
        # dropped the most, which are the only ones it exists for. Measured
        # while writing the fixture that grades it. It also reads better here: a
        # planner learns the screen is incomplete before it reads 225 rows
        # rather than after.
        #
        # WHAT ACTUALLY HAPPENED, not what the old single budget would have done.
        # "Only the first MAX_ELEMENTS are shown" became false in both
        # directions once the packet gained headroom: it denied elements it was
        # rendering on a screen that dropped nothing, and it understated a
        # 224-element loss as 150. And `truncated` alone cannot say WHICH side of
        # the panel was thinned, which is the difference between "ask for a
        # scroll" and "plan against this screen as if it were complete".
        inside = int(_count(content.get("dropped_inside_panel")))
        outside = int(_count(content.get("dropped_outside_panel")))
        unknown = int(_count(content.get("dropped_unclassified")))

        # THE BLOCK OWNS ITS OWN SIZE, so the count in the notice is true by
        # construction rather than by hoping nothing downstream trims it.
        #
        # There are TWO truncations here: the element budget in `prune`, and
        # `wrap_untrusted`'s character cap. A notice derived from the packet
        # knows only the first. Round 8f moved this notice ABOVE the element
        # lines so the cap would stop cutting the notice -- and the cap then cut
        # the LINES while the notice went on claiming them. Measured: 225
        # claimed, 181 present, and 45 on-panel elements absent while the notice
        # said 20 were lost on screen, which understates the tester's own loss
        # worse than the message that was replaced.
        #
        # So the lines are trimmed HERE, to what the budget can actually carry,
        # and the notice reports what was emitted. The cap can no longer cut an
        # element line, so `wrap_untrusted` cannot make this message false.
        rendered = render_lines(elements)
        # The SAME elements with no references, for the branch that cannot
        # afford the legend. Built once; the loop chooses between them.
        plain = [element_line(element) for element in elements]

        def _notice(shown_count: int, too_big: int) -> str:
            """The notice for a given outcome -- recomputed, never patched.

            It has to be a function of the count because the count is what the
            fixed point below changes: drop a line and both the shown number and
            the not-carried number move, so a notice composed once and reused is
            a notice about a block that no longer exists.
            """
            missing = inside + outside + unknown + too_big
            if missing:
                note = "...[%d elements shown; %d not carried" % (
                    shown_count,
                    missing,
                )
                if inside:
                    # The one a planner must act on: the screen it can SEE is
                    # incomplete, so the next step should scroll, not assume.
                    note += ", %d of them on this screen" % inside
                if outside:
                    note += ", %d off the display" % outside
                if unknown:
                    note += ", %d with no display to place them" % unknown
                if too_big:
                    # Deliberately NOT split by side of the panel: the split for
                    # the budget drops is known, this one is not, and claiming a
                    # precision we do not have is the defect this message has
                    # already had twice.
                    note += (
                        ", %d beyond the size of this message (some of those may "
                        "be on this screen too)" % too_big
                    )
                return note + "]"
            if content.get("truncated"):
                # `truncated` still means "the dump offered more than
                # MAX_ELEMENTS" -- it gates `screen_hit`'s press refusal and is
                # deliberately unchanged -- so it can be true while the headroom
                # carried everything. Say that, rather than claim a loss that
                # did not happen.
                return (
                    "...[%d elements shown; this screen exceeded the usual cap "
                    "but nothing was dropped]" % shown_count
                )
            return ""

        # A FIXED POINT, because the artifact must be measured AFTER the notice
        # is written. The previous version reserved a guessed number of
        # characters for the notice and trimmed against it -- and the notice grew
        # past the guess at three-digit counts, so `wrap_untrusted` cut an
        # element line after the count had already been printed. The cut landed
        # mid-`at[...]`, which hands the planner a rectangle in the SHRINKING
        # direction: worse than the false count it was meant to prevent.
        #
        # So: compose the whole block, measure it, drop the LAST element line,
        # and recompose -- until it fits. Terminates because `shown` strictly
        # shrinks. The header is dropped only as a last resort, and the notice is
        # never what goes: a notice the cap cuts off is not a notice.
        head_base = list(lines)
        # BOUNDED BEFORE THE LOOP. The loop re-joins the body each iteration, so
        # it is quadratic in the number of lines: measured 0.001s at 225
        # elements and 50s at 60000, which a stored packet can carry even though
        # `prune` cannot produce it. No live packet exceeds this bound, so the
        # cut costs nothing that was going to be rendered anyway.
        rendered = rendered[: MAX_ELEMENTS + MAX_PACKET_HEADROOM]
        plain = plain[: MAX_ELEMENTS + MAX_PACKET_HEADROOM]
        # REFERENCES ARE A PAIR: the `@id` notation and the legend that explains
        # it travel together or neither travels. The first version decided the
        # legend ONCE, from the full `rendered` list, and inserted it at head
        # index 1 -- above both summaries. Two things followed, and an
        # adversarial review measured both. Above the summaries, the head-drop
        # path ate `controls`, then `fields`, then every element line before it
        # touched the legend: 11 element lines with the legend suppressed, 0
        # with it present, on the same packet. And decided from `rendered`
        # rather than from what survives, it was emitted for references the tail
        # trim had already removed, and omitted while 7 surviving lines still
        # carried `text=@e0`.
        #
        # So the choice is made INSIDE the loop, from `shown`, and the pair
        # degrades in the order that costs the tester least. An unexplained
        # `@e0` is worse than the bytes it saves, but so is throwing the
        # notation away: measured, dropping the references to keep the paragraph
        # left ONE element line where the referenced form fit eleven, because a
        # plain line is three times longer. So the EXPLANATION degrades first
        # (paragraph, then one line), and only if even one line cannot be
        # afforded do the references go -- `plain` is the same elements with
        # none, which is why both lists are carried.
        STAGES = ((True, REFERENCE_LEGEND), (True, REFERENCE_LEGEND_SHORT), (False, ""))

        def _best(base: list) -> tuple:
            """The (text, count) that shows the MOST element lines under `base`.

            EVERY stage is measured and the widest wins -- it is not a
            first-fit. First-fit does not work here and the failure is silent:
            popping element lines eventually removes the last REFERENCE, at
            which point the legend is no longer needed, the block fits, and the
            loop stops -- with one element line, having never tried the cheaper
            explanation that would have carried eight. Measured on a packet with
            an 11540-character header. The tie goes to the earliest stage, which
            is the fullest explanation.
            """
            best_text = ""
            best_count = -1
            for use_refs, legend_text in STAGES:
                source = rendered if use_refs else plain
                count = len(source)
                while True:
                    body = source[:count]
                    legend = (
                        [legend_text]
                        if legend_text and any("=@" in x for x in body)
                        else []
                    )
                    note = _notice(count, len(source) - count)
                    text = "\n".join(base + legend + ([note] if note else []) + body)
                    if len(text) <= MAX_BLOCK_CHARS:
                        if count > best_count:
                            best_text, best_count = text, count
                        break
                    if not count:
                        break
                    count -= 1
            return best_text, best_count

        while True:
            text, count = _best(head_base)
            if count >= 0 and (count or len(head_base) <= 1):
                break
            if len(head_base) > 1:
                # THE FREED ROOM BELONGS TO THE ELEMENTS. Dropping a header line
                # without restoring them spent 11793 characters on nothing:
                # measured 0 of 20 elements emitted where 18 fitted, so the
                # planner got a screen with no actionable element and scrolled
                # or gave up instead of tapping.
                #
                # Controls first, FIELDS only if that was not enough -- the
                # fields summary is the only thing that says which element takes
                # typing, and it exists because a model tapped Voice mode as
                # Send. Neither is a convenience, but one is cheaper to lose.
                head_base = head_base[:-1]
                continue
            # LAST RESORT: one header line that still will not fit. Returning the
            # text measured as too long let `wrap_untrusted` slice it and take
            # the notice with it -- 5 elements and no warning at all, which is
            # the very failure the header drop above was added to prevent. So
            # make room for the notice explicitly and compose what is returned.
            note = _notice(0, len(plain))
            keep = MAX_BLOCK_CHARS - len(note) - 1
            head_base = [head_base[0][:keep]] if keep > 0 else []
            text = "\n".join(head_base + ([note] if note else []))
            break
        return wrap_untrusted("screen", text, limit=MAX_BLOCK_CHARS)
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile.perception.to_prompt_block failed")
        return ""
