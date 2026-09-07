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

# Per-attribute cap. Long enough for a paragraph of on-screen copy, short enough
# that one hostile node cannot dominate the packet.
MAX_ATTR_CHARS = 200

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
    """``(display, frame)`` -- the screen, and the region an element may occupy.

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
      a no-op and send a gesture the device silently ignores.
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
    :data:`MAX_DISPLAY_EXTENT`. Anything else, and the display IS the dump
    extent. ``(None, None)`` when the dump lays out nothing at all.
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
        # The panel estimate, NOT the full extent: see the second walk above.
        width, height = panel_right or right, panel_bottom or bottom

    if width <= 0 or height <= 0:
        return None, None
    return (0, 0, width, height), (0, 0, max(width, right), max(height, bottom))


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


def prune(xml: object, activity: str = "", display: object = None) -> dict:
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
        selected, frame = _display_rect(root, display, root.get("rotation"))
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

        # THE BUDGET IS SPENT ON THE DISPLAY FIRST.
        #
        # The frame above is the display WIDENED to cover everything the dump
        # lays out, which is what stops a stale device size deleting the
        # tester's screen. But widening admits off-display elements into the
        # same `MAX_ELEMENTS` budget, and they are appended in DOCUMENT order --
        # so an adjacent pager page or an incoming activity, laid out one screen
        # width away at positive coordinates, could fill the cap and evict the
        # control the tester is actually looking at. Measured: 150 rows of an
        # incoming activity listed first, and `Delete account` on the visible
        # screen was gone from the packet, with a CORRECT display.
        #
        # That is the round-3 defect wearing the round-5 fix's clothes, so the
        # answer is an ordering rule rather than another adjustment to the
        # rectangle: what the tester can SEE is never displaced by what they
        # cannot. Off-display elements keep whatever budget is left, in document
        # order, because they are still worth carrying -- see `_display_rect`.
        truncated = len(elements) > MAX_ELEMENTS
        if selected is not None and truncated:
            on_display = []
            off_display = []
            for element in elements:
                box = element.get("bounds") or []
                visible = len(box) == 4 and not (
                    box[0] >= selected[2]
                    or box[1] >= selected[3]
                    or box[2] <= selected[0]
                    or box[3] <= selected[1]
                )
                (on_display if visible else off_display).append(element)
            elements = on_display + off_display
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
            "truncated": truncated,
            "considered": considered,
        }
        return {"error": None, "content": content}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.perception.prune failed")
        return {"error": str(exc), "content": None}


def element_line(element: dict) -> str:
    """One pruned element as a single compact line."""
    parts = [str(element.get("id") or "?"), str(element.get("cls") or "?")]
    text = str(element.get("text") or "")
    desc = str(element.get("desc") or "")
    rid = str(element.get("rid") or "")
    if text:
        parts.append('text="' + text + '"')
    if desc and desc != text:
        parts.append('desc="' + desc + '"')
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
        elements = content.get("elements") or []
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
        if fields:
            lines.append(
                "fields (type into these): "
                + "; ".join(
                    str(element.get("id"))
                    + " "
                    + (str(element.get("label") or "") or "(unlabelled)")
                    for element in fields[:10]
                )
            )
        if controls:
            lines.append(
                "controls (tap these): "
                + "; ".join(
                    str(element.get("id"))
                    + " "
                    + str(element.get("label") or "")
                    + (
                        " [" + str(element.get("role")) + "]"
                        if element.get("role")
                        else ""
                    )
                    for element in controls[:20]
                )
            )
        lines.extend(element_line(element) for element in elements)
        if content.get("truncated"):
            lines.append(
                "...[only the first "
                + str(MAX_ELEMENTS)
                + " elements of this screen are shown]"
            )
        return wrap_untrusted("screen", "\n".join(lines), limit=MAX_BLOCK_CHARS)
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile.perception.to_prompt_block failed")
        return ""
