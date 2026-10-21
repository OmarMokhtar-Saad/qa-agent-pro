"""Name the near-miss when a text wait or optional tap fails on a substring-only label.

Waits and optional taps need an exact or whole-word label (class >= 2, D12), so
``setting`` does not meet on ``Settings``. A silent timeout then reads as "the
text is not on screen"; this names the label that holds it only as a substring.
Pure; never raises.
"""

from __future__ import annotations

from tools.mobile.act_select import match_class


def near_miss(screen: object, text: object) -> str:
    """``" ('x' appears only inside 'Y')"`` for a substring-only label, else ``""``."""
    want = " ".join(str(text or "").split()).lower()
    if not want or not isinstance(screen, dict):
        return ""
    for element in screen.get("elements") or []:
        if not isinstance(element, dict):
            continue
        for field in ("text", "desc"):
            label = str(element.get(field) or "").strip()
            if match_class(label, want) == 1:
                return f" ('{want[:60]}' appears only inside '{label[:60]}')"
    return ""
