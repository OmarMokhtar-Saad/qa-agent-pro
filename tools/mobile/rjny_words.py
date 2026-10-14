"""The four result words, their labels and tones, and the small text helpers around them."""

from __future__ import annotations

from typing import Any

from tools.mobile.report_text import _text
from tools.mobile.report_widgets import _pill

SUMMARY_ID = "qa-report-summary"


#: The four words a reader sees, in page order. Everything a case can say about
#: itself folds into one of them; the raw word stays on the pill's tooltip.
RESULTS = ("pass", "fail", "blocked", "unfinished")


RESULT_LABEL = {
    "pass": "Pass",
    "fail": "Fail",
    "blocked": "Blocked",
    "unfinished": "Unfinished",
}


RESULT_TONE = {"pass": "ok", "fail": "def", "blocked": "gap", "unfinished": "void"}


RESULT_PILL = {word: "p-" + tone for word, tone in RESULT_TONE.items()}


def _result_pill(word: str) -> str:
    return _pill(
        RESULT_PILL.get(word, "p-void"),
        RESULT_LABEL.get(word, word),
    )


def _count(n: int, one: str, many: str = "") -> str:
    return str(int(n)) + " " + (one if int(n) == 1 else (many or one + "s"))


def _finding_of(f: dict, manifest: dict, n: int) -> str:
    if f.get("finding"):
        return f["finding"]
    explore: Any = (
        manifest.get("explore") if isinstance(manifest.get("explore"), dict) else {}
    )
    for item in list(explore.get("findings") or []):
        if isinstance(item, dict) and str(item.get("turn")) == str(n):
            return _text(item.get("note"), 200)
    return f["title"] or f["tc_id"]
