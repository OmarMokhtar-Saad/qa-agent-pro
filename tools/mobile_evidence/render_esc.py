"""Escaping and the shared constants of the evidence fragments.

Every value reaches markup through :func:`ev_esc` -- scrub, neutralise the
untrusted-content guard markers, then ``html.escape(quote=True)``.
"""

from __future__ import annotations

import html

from tools.mobile_evidence import exchanges
from tools.mobile_shared import scrub
from tools.untrusted import GUARD_MARKERS, NEUTRALIZED

EVENTS_FILE = "events.ndjson"
#: The bound that actually applies to a rendered cell. `MAX_TEXT = 200` used to
#: sit here and was DEAD: it was the default argument of `_flat` and `ev_esc`,
#: and an AST scan found 0 of 65 call sites using the default -- every one passes
#: an explicit literal. Mutating it across nine orders of magnitude left the
#: rendered output byte-identical, so it asserted coverage of the report's cell
#: width while providing none, and hid the real widths, of which this is the
#: largest: host-supplied reply text going into the self-contained HTML.
MAX_REPLY_TEXT = 4000
MAX_ROWS = 400
CLIP = 64

#: The reason the page shows when the operator turned the capture off: a new report
#: must not READ a capture that is still on disk, or OFF would stop the writing but
#: not the showing. Nothing is deleted -- the files are the operator's to remove.
FLAG_OFF_REASON = "QA_MOBILE_APP_EVIDENCE is off: the capture on disk is not read"

#: The most this module will hold in memory from one run's evidence files, summed over
#: the event log and every logcat slice (review M3). Slices past the budget are NOT
#: read; the trust block names them. The per-file caps in capture stay as they are.
MAX_LOAD_BYTES = 24 * 1024 * 1024

#: Where one sample of each duration series was measured -- the tile's fold text.
SERIES = (
    ("llm", "Model reply time", "model calls"),
    ("api", "Backend call time", "backend calls"),
    ("tool", "Tool run time", "tool runs"),
    ("turn", "Time to answer, inside the app", "turns"),
)

MSEP = '<span class="msep"></span>'


def _neutralize(text: str) -> str:
    out = text
    for marker in GUARD_MARKERS:
        if marker and marker in out:
            out = out.replace(marker, NEUTRALIZED)
    return out


def flat(value: object, limit: int) -> str:
    raw = "" if value is None else str(value)
    kept = "".join(
        " " if c in "\t\r\n" else c for c in raw if c == " " or c.isprintable()
    )
    kept = " ".join(kept.split())
    if len(kept) > limit:
        kept = kept[:limit] + "..."
    return kept


def ev_esc(value: object, limit: int) -> str:
    """scrub -> neutralise guard markers -> escape. The ONE way a value reaches markup."""
    return html.escape(_neutralize(scrub.scrub_text(flat(value, limit))), quote=True)


def guard(markup: str) -> str:
    """The marker step for markup ``exchanges`` already escaped through ``scrub.e``."""
    return _neutralize(markup)


def clip(text: object, n: int = CLIP) -> str:
    one = flat(text, MAX_REPLY_TEXT)
    return one if len(one) <= n else one[: n - 1].rstrip() + "…"


def fmt(ms: object) -> str:
    return exchanges.fmt_ms(ms)


def status_label(b: dict) -> str:
    if b.get("statusKnown") and b.get("status") is not None:
        return str(b.get("status"))
    if b.get("ok"):
        return "2xx (code not logged)"
    if b.get("ok") is None:
        return "never returned"
    return "failed (code not logged)"
