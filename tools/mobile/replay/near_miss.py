"""Name the near-miss when a text wait or optional tap fails on a substring-only label.

Waits and optional taps need an exact or whole-word label (class >= 2, D12), so
``setting`` does not meet on ``Settings``. A silent timeout then reads as "the
text is not on screen"; this names the label that holds it only as a substring,
or, when none does, the label that holds it only in another Arabic/Persian
letter form. Both are hints: nothing here ever makes a text match.
Pure; never raises.
"""

from __future__ import annotations

from tools.mobile.act_select import match_class
from tools.mobile.text_fold import fold, skeleton


def _labels(screen: object) -> list:
    """Every text and desc label on *screen*, stripped, empties dropped."""
    elements = screen.get("elements") if isinstance(screen, dict) else None
    pairs = [
        (e.get("text"), e.get("desc")) for e in elements or [] if isinstance(e, dict)
    ]
    return [str(v).strip() for pair in pairs for v in pair if v and str(v).strip()]


def near_miss(screen: object, text: object) -> str:
    """``" ('x' appears only inside 'Y')"`` for a substring-only label, else a
    skeleton hint, else ``""``."""
    want = fold(text)
    if not want:
        return ""
    labels = _labels(screen)
    for label in labels:
        if match_class(label, want) == 1:
            return f" ('{want[:60]}' appears only inside '{label[:60]}')"
    return _skeleton_hint(labels, want)


def _skeleton_hint(labels: list, want: str) -> str:
    """Hint only: *want* sits inside a label once letter variants are unified."""
    bare = skeleton(want)
    for label in labels:
        if bare and match_class(label, want) == 0 and bare in skeleton(label):
            return f" ('{want[:60]}' looks like '{label[:60]}': same letters, a different Arabic/Persian form)"
    return ""
