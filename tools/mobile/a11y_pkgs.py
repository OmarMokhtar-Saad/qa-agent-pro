"""Which package names belong to a keyboard window."""

from __future__ import annotations

#: Whole dotted segments of an input-method package (Gboard, AOSP, Samsung,
#: SwiftKey); equal, not substring, so ``com.example.keyboardshop`` is an app.
_IME_SEGMENTS = frozenset({"inputmethod", "honeyboard", "swiftkey", "keyboard"})
#: The QA keyboard; ime_manifest is the lane sentinel, so it is not imported here.
_QA_IME = "qaagents.ime"


def is_ime(package: str) -> bool:
    """True for the package of a keyboard window (the QA keyboard or any other)."""
    low = package.lower()
    return low.endswith(_QA_IME) or bool(_IME_SEGMENTS & set(low.split(".")))
