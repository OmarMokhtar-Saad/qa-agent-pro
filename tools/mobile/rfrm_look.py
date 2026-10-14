"""Look resolution: which stored look a frame key names, and what to say when none."""

from __future__ import annotations

from tools.mobile.report_text import _text

NOT_CAPTURED = "this screen was not captured"


#: Said instead of drawing the wrong look. "Not captured" would be false here:
#: the screen WAS captured, several times, and this frame does not know which
#: of them it means.
AMBIGUOUS_LOOK = (
    "This screen was seen more than once and this frame does not record which "
    "look it means, so nothing is drawn rather than the most recent one."
)


# False for an observation key -- that already names its look, and the dash it
# carries is what tells the two apart. Counting the observations belonging to
# this screen is what lets a screen seen exactly once keep rendering, instead
# of being suppressed by a guard too coarse to tell the cases apart.
#
# THE DASH FAST PATH IS GONE, and its removal is the fix. It returned False
# for any ident carrying a dash, which is right for an observation key and
# WRONG for the screen id ``_document`` concedes is admissible: `_RUN_ID_RE`
# accepts ``aabbccdd0011-000000000000``, and such an id with two stored looks
# was answered "unambiguous" by the fast path and "ambiguous" by the count --
# one question derived twice inside one function, disagreeing. The count is
# the only derivation now. It still answers False for a real observation key,
# because the prefix it scans for is that key plus another dash and no library
# entry can start with it, so nothing about the ordinary case changed.
def _look_is_ambiguous(ident: str, library: dict) -> bool:
    """Does this BARE screen id stand for more than one stored look?"""
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


# THE resolver. A bare screen id says WHICH screen and cannot say WHAT was on
# it: the screen library holds the LAST look, and a screen observed more than
# once has several. Resolving a frame against it draws the newest content
# under whatever caption the caller wrote -- on a conversation, a reply the app
# had not yet produced, shown to a non-technical tester as evidence of what
# their own action did.
#
# ``LOOK_AMBIGUOUS`` means TWO OR MORE stored looks. Exactly one is not
# ambiguous -- the bare id and that single observation are the same bytes -- so
# a settings walk and every single-look screen resolve as they always did.
# That is what keeps this a guard and not a regression.
#
# The verdict is returned rather than acted on because the two consumers
# render it differently -- the fold draws an empty frame, the phone an empty
# handset -- and the point of this function is that neither of them decides
# the QUESTION.
def _resolve_look(screen_id: object, screens: object) -> tuple[str, dict | None, str]:
    """A frame key against the merged library: ``(ident, screen or None, verdict)``."""
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
