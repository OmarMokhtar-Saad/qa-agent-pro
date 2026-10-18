"""Verdict vocabulary: the done verdicts, tiles, tones, tallies and error phrasing."""

from __future__ import annotations

from typing import Any

from tools.mobile import run_store
from tools.mobile.rtxt_clean import _text, _token

#: The verdicts that mean a case is finished. An empty verdict is NOT one of
#: them, and :func:`_verdict_of` never invents one -- a case shown as passed
#: because a field was missing is the worst artifact a report can produce.
#: Re-exported from `run_store`, which is the layer that decides whether a
#: case will be handed out again. Two literals drifted once already.
DONE_VERDICTS = run_store.DONE_VERDICTS


#: Re-exported for the same reason ``DONE_VERDICTS`` is: ONE producer of
#: "reached a verdict", in the layer that owns what a verdict means, read here
#: and by the chat surface. A local copy is how the last such value came to
#: have three definitions.
verdict_coverage = run_store.verdict_coverage


coverage_phrase = run_store.coverage_phrase


#: Tiles always rendered, in this order, so a zero is visible rather than absent.
TILES = (
    "pass",
    "fail",
    "blocked",
    "unverified",
    "no_verdict",
    "needs_tester",
    "needs_model",
    "planning",
    "unknown",
)


#: How each verdict reads on the page: (swatch, segment, pill, label). Status
#: colours are reserved for state, and every colour ships with its word.
VERDICT_TONE = {
    "pass": ("ok", "s-pass", "p-ok", "Passed"),
    "fail": ("def", "s-def", "p-def", "Failed"),
    "blocked": ("gap", "s-gap", "p-gap", "Blocked"),
    "unverified": ("gap", "s-gap", "p-gap", "Not verified"),
    "no_verdict": ("void", "s-void", "p-void", "No verdict (nothing asserted)"),
    "needs_tester": ("void", "s-void", "p-void", "Needs the tester"),
    "needs_model": ("void", "s-void", "p-void", "Needs the model"),
    "planning": ("void", "s-void", "p-void", "Planning"),
    "unknown": ("void", "s-void", "p-void", "No verdict"),
}


_OTHER_TONE = ("void", "s-void", "p-void", "")


def _verdict_of(case: object) -> str:
    """The verdict to show, never invented.

    A terminal verdict wins. Otherwise the STATUS is shown (``planning``,
    ``needs_model``, ...) so a case in flight reads as in flight. An empty
    verdict with no status is ``unknown`` -- never ``pass``.
    """
    body = case if isinstance(case, dict) else {}
    verdict = _token(body.get("verdict"))
    if verdict in DONE_VERDICTS:
        return verdict
    status = _text(body.get("status"), 40)
    if _token(status) == "done":
        # Finished with nothing asserted: its own word, not "done" read as a verdict.
        return "no_verdict"
    return _token(status) if status else "unknown"


def _tone(verdict: str) -> tuple:
    sw, seg, pill_cls, label = VERDICT_TONE.get(verdict, _OTHER_TONE)
    return sw, seg, pill_cls, (label or verdict.replace("_", " "))


def tally(cases: Any) -> dict:
    """Counts per verdict, with every tile present at zero."""
    out = {name: 0 for name in TILES}
    for case in list(cases or []):
        key = _verdict_of(case)
        out[key] = out.get(key, 0) + 1
    return out


def _unify_verdict(raw: object) -> str:
    """``pass``/``fail``/``blocked`` pass through; every other word is ``unfinished``.

    ``unverified`` is a verdict the run could not stand behind, so it reads as
    blocked, not as a pass. In-flight states (``needs_model``, ``planning``,
    ``needs_tester``, ``done`` without a verdict) and ``unknown`` never finished.
    """
    word = _token(raw)
    if word in ("pass", "fail", "blocked"):
        return word
    if word == "unverified":
        return "blocked"
    return "unfinished"


def _app_logged(loaded: object) -> bool:
    """Whether the run holds an app log.

    When it does not, the Diagnostics "App evidence" part says why, once. Every
    case and turn repeating the same run-wide reason said nothing new.
    """
    content = loaded.get("content") if isinstance(loaded, dict) else None
    return isinstance(content, dict) and content.get("source") not in (None, "none")


def error_phrase(counts: object) -> str:
    """``"3 (1 dump_failed, 2 left_app)"`` -- or the named zero, never a blank."""
    book = counts if isinstance(counts, dict) else {}
    total = sum(book.values())
    if not total:
        return "0 - no action on this run ended for a device reason"
    return (
        str(total)
        + " ("
        + ", ".join(str(n) + " " + name for name, n in sorted(book.items()))
        + ")"
    )
