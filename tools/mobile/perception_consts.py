"""Fixed texts and vocabularies of the screen-perception lane."""

from __future__ import annotations

from tools.mobile.perception_limits import MAX_DUMP_BYTES
from tools.safe_parse import XML_DECL_RE

#: Emitted in the block head, and ONLY when a reference is actually present, so
#: a screen with no repetition pays nothing for the notation. It lives in the
#: SCREEN BLOCK rather than the system prompt because the screen block is on
#: every packet and the system prompt, since the per-chat contract landed, is
#: not.
REFERENCE_LEGEND = (
    "note: `@#7` in place of a string means this element's text (or `(@#7)` its "
    "desc) is the SAME string that line #7 prints in full above -- printed once "
    "to keep this screen readable. To act on such an element, target it by its "
    "own #n (or its id or role); to target it by text, copy the string from the "
    "line it is printed on. A `label=` is never abbreviated this way."
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
    "note: `@#7` means the same string line #7 prints in full above; "
    "target such an element by its #n."
)


# A node with no text, no description, no resource id and no affordance is pure
# layout scaffolding: it cannot be a target and it cannot be asserted on.
EDITABLE_CLASS_HINTS = (
    "EditText",
    "AutoCompleteTextView",
    "SearchView",
    "TextInputEditText",
)

_DECL_RE = XML_DECL_RE

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

#: Joins the raw and split forms of one id. ``executor.destructive_hit`` judges
#: each side of it on its own, so a multi-word term cannot match across the
#: junction (``OutBox_log`` raw ends ``log``, split starts ``out``: "log out").
SEGMENT_BREAK = "\x1f"

_LEGEND_STAGES = (
    (True, REFERENCE_LEGEND),
    (True, REFERENCE_LEGEND_SHORT),
    (False, ""),
)
