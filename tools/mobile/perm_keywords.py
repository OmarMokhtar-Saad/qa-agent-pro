"""Which permission group a pop-up message asks for (English and Arabic keywords).

Arabic words are written as ``\\u`` escapes: no right-to-left literal in source.
"""

from __future__ import annotations

_KEYWORDS = {
    "location": ("location", "\u0645\u0648\u0642\u0639"),
    "camera": (
        "take pictures",
        "camera",
        "\u0627\u0644\u0643\u0627\u0645\u064a\u0631\u0627",
        "\u0627\u0644\u062a\u0642\u0627\u0637 \u0627\u0644\u0635\u0648\u0631",
    ),
    "microphone": (
        "record audio",
        "microphone",
        "\u0627\u0644\u0645\u064a\u0643\u0631\u0648\u0641\u0648\u0646",
        "\u062a\u0633\u062c\u064a\u0644 \u0627\u0644\u0635\u0648\u062a",
    ),
    "notifications": (
        "notification",
        "\u0625\u0634\u0639\u0627\u0631",
        "\u0627\u0634\u0639\u0627\u0631",
    ),
    "contacts": (
        "contacts",
        "\u062c\u0647\u0627\u062a \u0627\u0644\u0627\u062a\u0635\u0627\u0644",
    ),
    "photos/storage": (
        "photos",
        "media",
        "files",
        "storage",
        "\u0627\u0644\u0648\u0633\u0627\u0626\u0637",
        "\u0627\u0644\u0645\u0644\u0641\u0627\u062a",
        "\u0627\u0644\u0635\u0648\u0631 \u0648\u0627\u0644\u0641\u064a\u062f\u064a\u0648",
    ),
    "phone": (
        "phone call",
        "call log",
        "\u0627\u0644\u0645\u0643\u0627\u0644\u0645\u0627\u062a",
        "\u0627\u0644\u0647\u0627\u062a\u0641",
    ),
    "sms": (
        "sms",
        "text message",
        "\u0627\u0644\u0631\u0633\u0627\u0626\u0644 \u0627\u0644\u0642\u0635\u064a\u0631\u0629",
        "\u0627\u0644\u0631\u0633\u0627\u0626\u0644 \u0627\u0644\u0646\u0635\u064a\u0629",
    ),
    "calendar": ("calendar", "\u0627\u0644\u062a\u0642\u0648\u064a\u0645"),
    "nearby/bluetooth": (
        "nearby devices",
        "bluetooth",
        "\u0627\u0644\u0623\u062c\u0647\u0632\u0629 \u0627\u0644\u0645\u062c\u0627\u0648\u0631\u0629",
        "\u0628\u0644\u0648\u062a\u0648\u062b",
    ),
    "body_sensors": (
        "body sensors",
        "heart rate",
        "\u0623\u062c\u0647\u0632\u0629 \u0627\u0644\u0627\u0633\u062a\u0634\u0639\u0627\u0631",
        "\u0645\u0639\u062f\u0644 \u0636\u0631\u0628\u0627\u062a \u0627\u0644\u0642\u0644\u0628",
    ),
    "activity": (
        "physical activity",
        "\u0627\u0644\u0646\u0634\u0627\u0637 \u0627\u0644\u0628\u062f\u0646\u064a",
    ),
}


def groups_in(text: object) -> frozenset:
    """The groups whose keywords appear in the pop-up *text* (case-insensitive)."""
    low = str(text or "").lower()
    return frozenset(
        group for group, words in _KEYWORDS.items() if any(w in low for w in words)
    )
