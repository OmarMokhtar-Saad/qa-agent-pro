"""A runtime-permission DECLINE is never a learned popup dismiss.

The permission dialog has its own rule (``dialog_permission``): it answers
"While using the app" / "Allow" and never declines. A learned dismiss that taps
"Not Allow" / "Don't allow" / "Deny" would contradict that rule, and a step does
not record the dialog's package, so the decline LABEL is what is recognised.
"""

from __future__ import annotations

import re

from tools.mobile.deny_terms import normalize

# Arabic "no permission" / "decline" written as \u escapes (no RTL literal in source).
_DECLINE = re.compile(
    r"(?<!\w)(?:not allow|don t allow|dont allow|do not allow|deny|permission deny\w*|"
    r"\u0639\u062f\u0645 \u0627\u0644\u0633\u0645\u0627\u062d|\u0631\u0641\u0636)(?!\w)"
)


def is_permission_decline(rid: object) -> bool:
    """True when the dismiss target reads as declining a permission."""
    return bool(_DECLINE.search(normalize(str(rid or ""))))
