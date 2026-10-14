"""The verdict strip, the crash block and the ended-at block of a case card."""

from __future__ import annotations

from tools.mobile.report_text import _stamp, esc
from tools.mobile.report_verdict import DONE_VERDICTS, _tone
from tools.mobile.report_widgets import _pill


# The app's own death, as evidence rather than as a verdict.
#
# ``cerr`` is an EXISTING rule in ``report_shell.html`` -- mono, pre-wrap,
# defect-coloured, which is exactly a log excerpt. No new class name is
# introduced on purpose: this module emits class names that the shell must
# style, and a class with no rule renders unstyled with nothing failing.
# ``esc`` neutralises guard markers before escaping, so device text cannot
# forge prompt scaffolding on the page either.
def _crash_block(crash: object) -> str:
    """The app's own death, as evidence rather than as a verdict."""
    body = crash if isinstance(crash, dict) else {}
    if not body.get("detected"):
        return ""
    return (
        '<div class="cerr">'
        + esc(body.get("label") or "the app under test died", 160)
        + " — "
        + esc(body.get("marker"), 200)
        + "\n"
        + esc(body.get("excerpt"), 1200)
        + "</div>"
    )


def _vstrip_why(facts: dict) -> str:
    """The sentence a verdict strip gives for its verdict."""
    if facts["verdict"] in DONE_VERDICTS:
        return (
            ("<b>" + esc(facts["reason"], 400) + "</b>")
            if facts["reason"]
            else "the run recorded no reason for this verdict"
        )
    return (
        "this case has not reached a verdict — its status is <b>"
        + esc(facts["status"] or "unknown", 40)
        + "</b>, so its own record is not a judgement"
    )


def _vstrip(facts: dict) -> str:
    verdict = facts["verdict"]
    _sw, _seg, pill_cls, label = _tone(verdict)
    tone = {"pass": "v-pass", "fail": "v-fail", "unverified": "v-fail"}.get(verdict, "")
    return (
        '<div class="vstrip '
        + tone
        + '"><span class="label">Verdict</span>'
        + _pill(pill_cls, label.lower())
        + '<span class="vwhy">'
        + _vstrip_why(facts)
        + "</span></div>"
        + _crash_block(facts.get("crash"))
    )


def _ended_row(label: str, value: str) -> str:
    return (
        '<div class="erow"><span class="elab">'
        + esc(label, 40)
        + "</span>"
        + value
        + "</div>"
    )


def _ended_rows(facts: dict) -> str:
    status = esc(facts["status"] or "unknown", 40)
    stamp = esc(_stamp(facts["updated"]), 40)
    _sw, _seg, pill_cls, label = _tone(facts["verdict"])
    none = "" if facts["last"] else " none"
    screen = esc(facts["last"], 40) or "none recorded"
    return "".join(
        [
            _ended_row("status", '<span class="cap">' + status + "</span>"),
            _ended_row("verdict", _pill(pill_cls, label.lower())),
            _ended_row(
                "escape-hatch turns",
                '<span class="cap">' + str(facts["escapes"]) + "</span>",
            ),
            _ended_row("last checkpoint", '<span class="cap">' + stamp + "</span>"),
            _ended_row(
                "last screen", '<span class="cap' + none + '">' + screen + "</span>"
            ),
        ]
    )


def _ended_block(facts: dict) -> str:
    return (
        '<div class="cmeta"><div><h4>Ended at</h4><div class="estate">'
        + _ended_rows(facts)
        + "</div></div></div>"
    )
