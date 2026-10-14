"""Kinds, caps, density and wording constants of the screen audit (leaf of screen_audit)."""

from __future__ import annotations

#: An interactive control that a screen-reader user cannot name at all.
KIND_UNNAMED_CONTROL = "unnamed_control"
#: An editable field with no accessible name. Split from the control kind on
#: purpose: the two domains are DISJOINT (editable first, clickable second), so
#: each clause has screens only it can reject and neither grades the other.
KIND_UNLABELLED_FIELD = "unlabelled_field"
#: An interactive target below the platform minimum, in pixels at a stated density.
KIND_SMALL_TARGET = "small_target"
#: Two or more interactive controls a user navigating by NAME cannot tell apart.
KIND_AMBIGUOUS_NAME = "ambiguous_name"
#: A password field whose dump carries the secret it holds.
KIND_SECURE_LABEL_LEAK = "secure_label_leak"

KIND_LABELS = {
    KIND_UNNAMED_CONTROL: "control with no accessible name",
    KIND_UNLABELLED_FIELD: "input field with no label",
    KIND_SMALL_TARGET: "touch target below the platform minimum",
    KIND_AMBIGUOUS_NAME: "controls sharing one accessible name",
    KIND_SECURE_LABEL_LEAK: "password field exposing its own value",
}

#: Findings carried out of one screen. The report renders every one into a
#: self-contained HTML file and the packet quotes the summary to a model.
MAX_FINDINGS = 40
#: Characters of any one finding's detail line, on both surfaces. It must be
#: large enough to carry :data:`ASSUMED_DENSITY_CAVEAT` WHOLE: a cap that clips
#: the words "this may be a false alarm" off the end of a warning leaves a
#: confident finding behind, which is worse than no warning at all. Pinned by
#: ``test_the_assumed_density_caveat_survives_the_character_cap``.
MAX_FINDING_CHARS = 400
#: Android's stated minimum touch target, in dp. The harmful direction is
#: UPWARD: a larger minimum flags targets that are fine.
MIN_TOUCH_TARGET_DP = 48
#: The definition of dp: 1dp is one pixel at 160dpi. Not a bound, a unit.
DENSITY_BASELINE_DPI = 160
#: The density used ONLY when the device did not answer. See the module
#: docstring for why 320 and why the fallback wording is not optional.
ASSUMED_DENSITY_DPI = 320
#: The dp minimum in pixels at :data:`ASSUMED_DENSITY_DPI` -- the FALLBACK floor
#: only; a device that answers gets its own floor computed from its own dpi. A
#: LITERAL, not a computed value: the cap scanner only sees ``NAME = <number>``,
#: so a derived constant would ship unbounded from above. The relation is pinned
#: by ``tests/mobile/test_mobile_screen_audit.py``.
MIN_TOUCH_TARGET_PX = 96

DENSITY_ASSUMED = "assumed"
DENSITY_DEVICE = "device"

#: The second half of an assumed-density finding, and the reason this module
#: does not simply print the number. ONE producer for the sentence, so the
#: packet and the report cannot warn differently -- or one of them not at all.
ASSUMED_DENSITY_CAVEAT = (
    " -- the device did not report its density, so this is an ASSUMPTION. On a "
    "low-density device (160-240dpi) a fully COMPLIANT 48dp control is only "
    "48-72px and would be flagged here, so confirm this one against the device "
    "before filing it."
)

NO_NODES_NOTE = (
    "This screen exposed no accessibility nodes, so NOTHING was audited -- that "
    "is not a clean result. A canvas-drawn app (Flutter, React Native, a game, "
    "a WebView) looks like this; judge it from the screenshot instead."
)
CLEAN_NOTE = "Every element this screen carried was audited; no check fired."

#: Resource-id words that name a widget rather than its purpose. A control whose
#: id reduces to these has no name derivable from it.
GENERIC_ID_WORDS = frozenset(
    {
        "btn",
        "button",
        "container",
        "content",
        "control",
        "edit",
        "field",
        "frame",
        "group",
        "icon",
        "image",
        "img",
        "item",
        "iv",
        "layout",
        "root",
        "row",
        "text",
        "tv",
        "view",
        "widget",
        "wrapper",
    }
)

#: Characters an app uses to MASK a password in its own dump. Text made only of
#: these is a mask, not a leak.
MASK_CHARS = frozenset("*•·●∙ ")
