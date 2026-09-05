"""The standalone HTML run report, built from a run's own files and nothing else.

``render(run_id)`` reads ``runs/<run_id>/manifest.json``, ``cases/TC-*.json``,
``screens/<screen_id>.json`` and ``lease.json`` -- and NOTHING else. No device,
no ``suite_store``, no in-memory state left by a previous call. That is what
makes a MID-RUN report possible: a half-finished run is simply a run whose
checkpoints are not all terminal yet, and the page says so (``partial``).

**The page is the Sara report shell.** ``report_shell.html`` beside this module
is the design file the reference device-run report is drawn with -- the
same tokens, the same appbar, masthead, KPI tiles, filter toolbar, expandable
case cards and phone frames, and the same script behind them -- so a reader who
has learned that page can read this one without relearning anything. This
module owns the DATA only: it fills the shell's ``{{SLOT}}``s with markup built
from the run's files. A colour never belongs here, and a number never belongs
in the shell.

**Every string in here was chosen by an app on the device.** A dump's ``text``,
``content-desc`` and ``resource-id`` are attacker-influenced, and this is an
HTML document, so the exposure is XSS in the tester's browser rather than only
prompt spoofing. ``perception._clean`` caps length and neutralises the guard
markers; it does NOT escape HTML. Three layers answer that, in this order:

1. **The one ``script`` element's text is :data:`SHELL_SCRIPT`, read from the
   shell file, with no interpolation of any kind -- and no event-handler
   attribute is ever emitted.** The script is the shell's own behaviour (theme
   switch, filter, sort, deep links); nothing a store value carries can reach
   it, and a test asserts the page's script text equals the constant, so any
   attacker byte reaching it makes the two differ. The store islands the shell
   supports (``<script type="text/plain">``) are NEVER emitted by this lane.
2. **The one ``style`` element's text is :data:`SHELL_STYLE`, likewise read
   from the shell, with no interpolation** -- no f-string, no ``%``, no
   ``.format``. The CSS context is absent by construction, and a test asserts
   the page's style text equals the constant.
3. **Every interpolated value goes through :func:`esc`** =
   ``html.escape(_text(value), quote=True)``. ``quote=True`` is load-bearing:
   attribute values are quoted and ``&quot;``/``&#x27;`` is what stops a value
   closing its own attribute.

Slots are filled in ONE regex pass over the template (:func:`_fill`), so a
value that happens to contain ``{{SECTIONS}}`` is never itself expanded.

Geometry is the one thing not escaped and does not need to be: a rect's
``style`` carries only ``int``s this module computed (:func:`scale_bounds`),
never a store string, and a test pins that they really are ``int``.

**Secrets.** The store already writes ``***`` for marked values and masks
credential-NAMED keys regardless, so on a file this tree wrote the extra
:func:`run_store.redact` call below changes nothing. It earns its place on the
three inputs the store did not write -- a checkpoint from another build, a file
someone edited, a run directory copied from another machine. It CANNOT catch a
value stored under an unrecognised key with no marker; ``run_store``'s own
docstring says so and this module does not claim more. Two further, structural
reductions: the report never prints a typed literal (``text``) from an action
and never JSON-dumps a trace entry, so a credential in an action's own text has
no route into the page and the token ``secret`` never appears in it at all.

**The kill-switch is read here.** Writing a file is not in
``tests/mobile/test_mobile_killswitch_surface.py``'s ``EFFECT_CALLS``, so
nothing mechanical binds this module -- but ``report_selfcheck`` is reachable as
``python3.12 -m tools.mobile.report_selfcheck <run_id>`` from outside the MCP
process entirely, which is the exact shape that produced three review rounds in
this programme (``provisioner --apply``, the extracted ``emulator.start``,
``session.start_install``). A guard on a caller is only as good as the list of
callers, and for a module with a ``-m`` entry point that list includes the
shell. ``run_store.write_case`` is unguarded and that asymmetry is deliberate:
it has no entry point of its own.
"""

from __future__ import annotations

import hashlib
import html
import logging
import os
import re
import time
from collections import Counter
from pathlib import Path

from config.settings import settings
from tools.mobile import actions as actions_mod
from tools.mobile import paths, run_store, screen_phone
from tools.mobile_evidence import exchanges as ev_exchanges
from tools.mobile_evidence import profiles as ev_profiles
from tools.mobile_evidence import render as ev_render

logger = logging.getLogger(__name__)

REPORT_FILE = "report.html"

#: The design file. Read once, at import: a shell that cannot be read is a
#: broken install, and it should fail loudly here rather than on the first run.
SHELL_PATH = Path(__file__).with_name("report_shell.html")

#: The phone frame every screen is scaled into. Fixed on purpose: a report whose
#: frames differ per screen cannot be compared by eye, which is the only thing a
#: wireframe is for.
FRAME_W = 360
FRAME_H = 800

#: ``perception.MAX_ELEMENTS`` / ``MAX_ATTR_CHARS``, enforced INDEPENDENTLY here
#: because a store file is an outside input by the time this module reads it.
MAX_RECTS = 150
MAX_TEXT = 200
MAX_ROWS = 400

#: The selfcheck's page-size pin, shared so the two cannot drift.
MAX_PAGE_BYTES = 8 * 1024 * 1024

SUMMARY_ID = "qa-report-summary"
END_ID = "qa-report-end"

#: The verdicts that mean a case is finished. An empty verdict is NOT one of
#: them, and :func:`_verdict_of` never invents one -- a case shown as passed
#: because a field was missing is the worst artifact a report can produce.
#: Re-exported from `run_store`, which is the layer that decides whether a
#: case will be handed out again. Two literals drifted once already.
DONE_VERDICTS = run_store.DONE_VERDICTS

#: Tiles always rendered, in this order, so a zero is visible rather than absent.
TILES = (
    "pass",
    "fail",
    "blocked",
    "unverified",
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
    "needs_tester": ("void", "s-void", "p-void", "Needs the tester"),
    "needs_model": ("void", "s-void", "p-void", "Needs the model"),
    "planning": ("void", "s-void", "p-void", "Planning"),
    "unknown": ("void", "s-void", "p-void", "No verdict"),
}
_OTHER_TONE = ("void", "s-void", "p-void", "")
RAIL = {"pass": "ok", "fail": "def", "blocked": "gap", "unverified": "gap"}

#: The same three bands the reference report uses for a case's wall clock.
LAT_BUCKETS = (
    ("fast", "under 3s", 3000),
    ("ok", "3-8s", 8000),
    ("slow", "over 8s", None),
)

#: The row kind a trace op is drawn as. The kinds are the shell's own vocabulary
#: (``.seq.is-<kind>`` is what the stylesheet colours by).
OP_KIND = {
    "tap": "tap",
    "press": "tap",
    "back": "tap",
    "type": "step",
    "set": "step",
    "clear": "step",
    "swipe": "step",
    "scroll": "step",
    "assert": "event",
    "wait": "log",
    "done": "done",
    "escape": "note",
    "ask": "note",
    "needs": "note",
}

FLAG_NAME = "QA_MOBILE_RUN_ENABLED"

FLAG_REFUSAL = (
    "Nothing was written. Building a mobile run report needs `"
    + FLAG_NAME
    + "=true` in `.env` and an MCP server restart (quit and reopen the editor)."
)

NOT_CAPTURED = "this screen was not captured"

_SAFE_TOKEN = re.compile(r"[^a-z0-9_]+")
_SLOT = re.compile(r"\{\{([A-Z_]+)\}\}")
_SLUG = re.compile(r"[^A-Za-z0-9_-]+")


def _read_shell() -> str:
    return SHELL_PATH.read_text(encoding="utf-8")


def _between(text: str, open_tag: str, close_tag: str) -> str:
    start = text.index(open_tag) + len(open_tag)
    return text[start : text.index(close_tag, start)]


#: The design, verbatim. Neither constant is ever interpolated into.
SHELL = _read_shell()
SHELL_STYLE = _between(SHELL, "<style>", "</style>")
SHELL_SCRIPT = _between(SHELL, "\n<script>", "</script>")

#: The only external references the shell makes: the typefaces the reference
#: report is set in. Each face carries a system fallback, so the page still
#: opens offline -- in a different face, with the same layout.
FONT_HOSTS = ("https://fonts.googleapis.com", "https://fonts.gstatic.com")


def _text(value: object, limit: int = MAX_TEXT) -> str:
    """Coerce, drop control characters, flatten whitespace, cap.

    Independent of ``perception._clean``: by the time this module reads a store
    file, that file is an outside input again.
    """
    raw = "" if value is None else str(value)
    kept = "".join(
        " " if character in "\t\r\n" else character
        for character in raw
        if character == " " or character.isprintable()
    )
    kept = " ".join(kept.split())
    if len(kept) > limit:
        kept = kept[:limit] + "..."
    return kept


def _neutralize_markers(text: str) -> str:
    """Blunt an untrusted-content or guard marker carried in a store value.

    ``perception`` does this for SCREEN attributes, but a case's title, reason
    and trace detail never pass through it -- they come back off disk, which
    :func:`_text`'s docstring already calls an outside input again. So a hostile
    app label reached the page with the sentinel intact, and this module's own
    selfcheck caught it on a real run while 937 tests stayed green, because no
    fixture had put the sentinel in a case field.

    The markers are IMPORTED, never restated. A second copy of that literal is
    the drift already fixed once inside ``perception``, where a hardcoded copy
    would have stopped matching a reworded guard with no test noticing.
    """
    from tools.mobile import perception

    out = text
    for marker in perception.GUARD_MARKERS:
        if marker and marker in out:
            out = out.replace(marker, perception.NEUTRALIZED)
    return out


def esc(value: object, limit: int = MAX_TEXT) -> str:
    """The ONE way a store value reaches markup. ``quote=True`` is deliberate.

    Escaping alone is not enough here. It makes markup inert, which protects the
    BROWSER, but leaves a guard sentinel readable, which does not protect a
    reader -- or a model asked to summarise the report later. So markers are
    neutralised before escaping.
    """
    return html.escape(_neutralize_markers(_text(value, limit)), quote=True)


def _token(value: object) -> str:
    """A store string reduced to something safe to use as a class or attribute.

    A verdict comes from a file, and this module puts it in a CLASS name and in
    a ``data-`` ATTRIBUTE NAME. An attribute name cannot be escaped, so it is
    normalised instead: ``[a-z0-9_]`` only, capped, ``unknown`` when nothing
    survives.
    """
    reduced = _SAFE_TOKEN.sub("_", _text(value, 40).lower()).strip("_")
    return reduced[:24] or "unknown"


def _slug(value: object) -> str:
    """A case id as a DOM id / URL fragment: ``[A-Za-z0-9_-]`` only, capped."""
    reduced = _SLUG.sub("-", _text(value, 40)).strip("-")
    return reduced[:40] or "case"


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
    return _token(status) if status else "unknown"


def _tone(verdict: str) -> tuple:
    sw, seg, pill_cls, label = VERDICT_TONE.get(verdict, _OTHER_TONE)
    return sw, seg, pill_cls, (label or verdict.replace("_", " "))


def tally(cases: object) -> dict:
    """Counts per verdict, with every tile present at zero."""
    out = {name: 0 for name in TILES}
    for case in list(cases or []):
        key = _verdict_of(case)
        out[key] = out.get(key, 0) + 1
    return out


def _bounds_of(element: object) -> tuple | None:
    """``[x1,y1,x2,y2]`` -> a normalised tuple, or None.

    Inverted bounds are SORTED rather than rejected: an app that reports
    ``[400,900,100,200]`` has still told us where its control is, and a negative
    width would otherwise reach the geometry.
    """
    body = element if isinstance(element, dict) else {}
    raw = body.get("bounds")
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    numbers = []
    for value in raw:
        if isinstance(value, bool):
            return None
        try:
            numbers.append(int(float(value)))
        except (TypeError, ValueError):
            return None
    left, right = sorted((numbers[0], numbers[2]))
    top, bottom = sorted((numbers[1], numbers[3]))
    return (left, top, right, bottom)


def _device_size(screen: object) -> tuple:
    """The device viewport, derived from the screen's own elements.

    Not a hardcoded phone size: a tablet AVD is legitimate. ``(0, 0)`` when the
    dump carries no usable geometry, which is what stops the divide.
    """
    body = screen if isinstance(screen, dict) else {}
    width = 0
    height = 0
    for element in body.get("elements") or []:
        box = _bounds_of(element)
        if box is None:
            continue
        width = max(width, box[2])
        height = max(height, box[3])
    return width, height


def scale_bounds(box: object, dev_w: int, dev_h: int) -> tuple | None:
    """Device pixels -> a rect inside the fixed frame, or None.

    Guarantees, unconditionally, for every tuple it returns: all four values are
    ``int``, ``x >= 0``, ``y >= 0``, ``x + w <= FRAME_W`` and
    ``y + h <= FRAME_H``. A malformed dump must not be able to paint outside the
    frame or raise.
    """
    try:
        if int(dev_w) <= 0 or int(dev_h) <= 0:
            return None
        left, top, right, bottom = (int(value) for value in box)
        left = min(max(0, left), int(dev_w))
        right = min(max(0, right), int(dev_w))
        top = min(max(0, top), int(dev_h))
        bottom = min(max(0, bottom), int(dev_h))
        if right <= left or bottom <= top:
            return None
        scale = min(FRAME_W / float(dev_w), FRAME_H / float(dev_h))
        x = int(left * scale)
        y = int(top * scale)
        w = max(1, int((right - left) * scale))
        h = max(1, int((bottom - top) * scale))
        x = min(x, FRAME_W - 1)
        y = min(y, FRAME_H - 1)
        w = min(w, FRAME_W - x)
        h = min(h, FRAME_H - y)
        return (x, y, w, h)
    except (TypeError, ValueError):
        return None


def wireframe(screen: object) -> dict:
    """``{"rects", "device", "scaled"}`` for one pruned screen.

    ``scaled`` is False when the dump had no usable geometry -- the report then
    draws a labelled empty frame rather than a plausible-looking wrong one.
    """
    body = screen if isinstance(screen, dict) else {}
    dev_w, dev_h = _device_size(body)
    if dev_w <= 0 or dev_h <= 0:
        return {"rects": [], "device": [dev_w, dev_h], "scaled": False}
    rects = []
    for element in list(body.get("elements") or [])[:MAX_RECTS]:
        box = _bounds_of(element)
        if box is None:
            continue
        placed = scale_bounds(box, dev_w, dev_h)
        if placed is None:
            continue
        holder = element if isinstance(element, dict) else {}
        label = _text(holder.get("text") or holder.get("desc") or holder.get("rid"), 40)
        kind = "plain"
        if holder.get("clickable"):
            kind = "tap"
        elif holder.get("editable"):
            kind = "edit"
        rects.append(
            {
                "x": placed[0],
                "y": placed[1],
                "w": placed[2],
                "h": placed[3],
                "label": label,
                "kind": kind,
            }
        )
    return {"rects": rects, "device": [dev_w, dev_h], "scaled": True}


# ── numbers ────────────────────────────────────────────────────────────────────


def _ms(value: object) -> int | None:
    """A duration from a store field, or None when it is not a measurement.

    A ZERO is not a measurement either. The runner writes ``ms: 0`` for an action
    it did not time -- a 4000 ms wait carries it -- and a bar of width zero would
    be a measurement of zero, which is the one thing this page must not draw.
    """
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0 or number != number:  # NaN
        return None
    return int(min(number, 10**9))


def fmt_ms(ms: object) -> str:
    """``412 ms`` under a second, ``2.3s`` at a second and over, ``1.4m`` past a minute."""
    number = _ms(ms)
    if number is None:
        return "—"
    if number < 1000:
        return str(number) + " ms"
    if number < 60000:
        return "%.1fs" % (number / 1000.0)
    return "%.1fm" % (number / 60000.0)


def _lat_bucket(ms: object) -> str:
    number = _ms(ms)
    if number is None:
        return ""
    for key, _label, ceiling in LAT_BUCKETS:
        if ceiling is None or number < ceiling:
            return key
    return LAT_BUCKETS[-1][0]


def _percentile(values: list, share: float) -> int:
    ordered = sorted(values)
    if not ordered:
        return 0
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * share))))
    return int(ordered[index])


def _stamp(value: object) -> str:
    try:
        moment = float(value or 0)
    except (TypeError, ValueError):
        return "unknown"
    if moment <= 0:
        return "unknown"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(moment))


def _short_stamp(value: object) -> str:
    try:
        moment = float(value or 0)
    except (TypeError, ValueError):
        return "unknown"
    if moment <= 0:
        return "unknown"
    return time.strftime("%d %b %Y %H:%M", time.localtime(moment))


# ── the shell's vocabulary, as the reference generator spells it ───────────────


def _pill(cls: str, label: object) -> str:
    return '<span class="pill ' + esc(cls, 24) + '">' + esc(label, 60) + "</span>"


def _metric(value: object, label: str, cls: str = "") -> str:
    return (
        '<span class="m '
        + esc(cls, 24)
        + '"><b>'
        + esc(value, 40)
        + "</b><i>"
        + esc(label, 40)
        + "</i></span>"
    )


MSEP = '<span class="msep"></span>'


def _kpi(
    value: str, label: str, detail: str = "", tone: str = "", hero: bool = False
) -> str:
    """``value`` and ``detail`` arrive as markup already built through :func:`esc`."""
    return (
        '<div class="kpi'
        + (" hero" if hero else "")
        + '"><div class="kv'
        + ((" " + esc(tone, 24)) if tone else "")
        + '">'
        + value
        + '</div><div class="kl">'
        + esc(label, 80)
        + "</div>"
        + (('<div class="kd">' + detail + "</div>") if detail else "")
        + "</div>"
    )


def _chip(group: str, value: str, label: str, count: int, sw: str = "") -> str:
    return (
        '<button type="button" class="chip" data-group="'
        + esc(group, 24)
        + '" data-v="'
        + esc(value, 60)
        + '" aria-pressed="false">'
        + (('<i class="sw ' + esc(sw, 24) + '"></i>') if sw else "")
        + esc(label, 60)
        + "<i>"
        + str(int(count))
        + "</i></button>"
    )


def _all_chip(group: str, count: int) -> str:
    return (
        '<button type="button" class="chip on" data-group="'
        + esc(group, 24)
        + '" data-v="*" aria-pressed="true">All<i>'
        + str(int(count))
        + "</i></button>"
    )


def _filter_group(label: str, chips: str) -> str:
    if not chips:
        return ""
    return (
        '<div class="fg"><span class="fgl">'
        + esc(label, 40)
        + "</span>"
        + chips
        + "</div>"
    )


def _sec_block(
    title: str, body: str, count: object = None, note: str = "", open_: bool = False
) -> str:
    return (
        '<div class="apis"><details class="sec"'
        + (" open" if open_ else "")
        + '><summary><span class="chev" aria-hidden="true"></span>'
        + esc(title, 80)
        + (
            ('<span class="cnt">' + str(int(count)) + "</span>")
            if count not in (None, "")
            else ""
        )
        + (('<span class="mt">' + esc(note, 160) + "</span>") if note else "")
        + '</summary><div class="secbody">'
        + body
        + "</div></details></div>"
    )


def _sechead(sid: str, title: str, label: str, lede: str, body: str) -> str:
    """``lede`` is markup built through :func:`esc`; the ids are constants."""
    return (
        '\n  <section id="'
        + sid
        + '">\n    <div class="sechead"><h2>'
        + esc(title, 80)
        + '</h2><span class="label">'
        + esc(label, 80)
        + "</span></div>\n"
        + (('    <p class="lede">' + lede + "</p>\n") if lede else "")
        + body
        + "\n  </section>\n"
    )


# ── the wireframe phone ────────────────────────────────────────────────────────


def _frame_html(screen_id: object, screens: object) -> str:
    """One frame, or an honest empty one when the screen was not stored."""
    ident = _text(screen_id, 40)
    library = screens if isinstance(screens, dict) else {}
    if not ident:
        return (
            '<div class="frame missing"><p class="wirenote">'
            + esc(NOT_CAPTURED)
            + "</p></div>"
        )
    screen = library.get(ident)
    if not isinstance(screen, dict):
        return (
            '<div class="frame missing" data-screen="'
            + esc(ident, 40)
            + '"><p class="wirenote">'
            + esc(NOT_CAPTURED)
            + "</p></div>"
        )
    frame = wireframe(screen)
    rects = "".join(
        '<div class="rect '
        + esc(rect["kind"], 12)
        + '" style="left:'
        + str(int(rect["x"]))
        + "px;top:"
        + str(int(rect["y"]))
        + "px;width:"
        + str(int(rect["w"]))
        + "px;height:"
        + str(int(rect["h"]))
        + 'px" title="'
        + esc(rect["label"], 40)
        + '" dir="auto">'
        + esc(rect["label"], 40)
        + "</div>"
        for rect in frame["rects"]
    )
    return (
        '<div class="frame" data-screen="'
        + esc(ident, 40)
        + '" data-rects="'
        + str(len(frame["rects"]))
        + '" data-scaled="'
        + ("1" if frame["scaled"] else "0")
        + '">'
        + rects
        + "</div>"
    )


def _phone_html(
    title: str, sub: str, screen_id: object, screens: object, app: str
) -> str:
    """The screen as the app drew it, with the element map folded beneath it.

    Both pictures come from the same pruned dump. ``screen_phone.compose`` reads
    the elements' text, labels and bounds into the app bar, bubbles, cards, chips
    and composer the user saw; the wireframe keeps the exact rectangles for
    anyone who needs them. Neither is a screenshot, and the caption says so.
    """
    ident = _text(screen_id, 40)
    library = screens if isinstance(screens, dict) else {}
    screen = library.get(ident) if ident else None
    if isinstance(screen, dict):
        drawn = screen_phone.compose(screen, esc=esc, app=app)
        fold = (
            '<details class="sec"><summary><span class="chev" aria-hidden="true"></span>'
            'Element map<span class="mt">the same screen as its element rectangles</span></summary>'
            '<div class="secbody"><div class="phone wire"><div class="ph-scroll">'
            + _frame_html(screen_id, screens)
            + "</div></div>"
            + _WIRE_LEGEND
            + "</div></details>"
        )
    else:
        drawn = (
            '<div class="phone" dir="auto"><div class="ph-scroll"><div class="ph-empty">'
            + esc(NOT_CAPTURED)
            + "</div></div></div>"
        )
        fold = ""
    return (
        '<div class="phone-col"><h4>'
        + esc(title, 40)
        + '<span class="mt">'
        + esc(sub, 80)
        + "</span></h4>"
        + drawn
        + fold
        + "</div>"
    )


_WIRE_LEGEND = (
    '<ul class="wirelegend"><li><i class="sw tap"></i>tappable</li>'
    '<li><i class="sw edit"></i>editable</li><li><i class="sw"></i>other element</li></ul>'
)


# ── the trace ──────────────────────────────────────────────────────────────────


def _action_line(action: object) -> str:
    """One human line for a trace action, including what it typed.

    **This docstring described the opposite rule until 2026-09-04, and the rule
    it described was itself the defect.** The typed ``text`` was never rendered,
    on the reasoning that a rendering which cannot reach a credential beats one
    that masks it. The cost was that a chat case's report showed ``type -> e14``
    and no tester could tell which question had been asked -- for a lane whose
    whole job is to evidence that the app answered, that is not a report.

    So the control moved from absence to masking, and it is doubled:

    * :func:`tools.mobile.actions.redact_action` masks at the SOURCE -- on the
      ``secret`` marker and on ``CREDENTIAL_TERMS`` -- so the packet, the audit
      log and the checkpoint are covered, not only this page;
    * this function masks again at the RENDER boundary, because a checkpoint
      written by an older build was never through that rule.

    The mutation proof lives on the mask below -- delete it, or either half of
    its condition, and the secret tests in tests/mobile/test_mobile_report.py
    and test_mobile_report_chat.py go red. It does NOT live on the
    ``run_store.redact`` call in :func:`_trace_rows`: deleting that still
    changes no byte of the page, measured, because the mask here fires first.
    Said plainly because the first version of this docstring claimed otherwise.
    """
    body = action if isinstance(action, dict) else {}
    if not body:
        return _text(action, 80)
    target = body.get("target")
    target = target if isinstance(target, dict) else {}
    # `rid` first, then what was on screen, and the short `id` LAST -- the same
    # order `actions.resolve_target` uses, and for the reader's sake rather
    # than the resolver's: an id is a content hash, so a step table that put it
    # first read `tap -> e9f3a1b2` where the model had also given a resource id
    # a tester could recognise.
    hint = _text(
        target.get("rid")
        or target.get("text")
        or target.get("desc")
        or target.get("id"),
        60,
    )
    extra = _text(body.get("kind") or body.get("field") or body.get("dir"), 40)
    parts = [_text(body.get("op"), 24) or "?"]
    if hint:
        parts.append("-> " + hint)
    if extra:
        parts.append("(" + extra + ")")
    typed = _typed_literal(body)
    if typed:
        parts.append("\u201c" + typed + "\u201d")
    return " ".join(parts)


def _typed_literal(body: dict) -> str:
    """What a ``type`` action sent, or the mask. ``""`` for any other op."""
    if str(body.get("op") or "") != "type":
        return ""
    if body.get("secret") or actions_mod.is_credential_action(body):
        return actions_mod.SECRET_MASK
    return _text(body.get("text"), 160)


def _trace_rows(trace: object) -> list:
    """The step table's rows, each re-redacted before anything is rendered."""
    rows = []
    for entry in list(trace or [])[:MAX_ROWS]:
        if not isinstance(entry, dict):
            continue
        # STILL NOT canary-provable, and this comment says so because MUTATION
        # SAID SO -- not because anyone reasoned about it. The claim written
        # here first was that rendering a typed literal had made this line
        # load-bearing. Deleting it and running the suite kept every test
        # green, because `_action_line` masks on the `secret` marker and on
        # CREDENTIAL_TERMS ITSELF, before this ever mattered. The structural
        # control is still the one that holds.
        #
        # It is kept as depth for the day a field IS rendered that only
        # key-based redaction covers, and the honest note is the point: a
        # security comment that overstates its own line is how a control gets
        # trusted for something it does not do.
        safe = run_store.redact(entry)
        safe = safe if isinstance(safe, dict) else {}
        action = safe.get("action")
        action = action if isinstance(action, dict) else {}
        rows.append(
            {
                "index": _text(safe.get("index"), 8),
                "op": _text(action.get("op"), 24) or "?",
                "action": _action_line(safe.get("action")),
                "outcome": _text(safe.get("outcome"), 40) or "-",
                "ms": _ms(safe.get("ms")),
                "detail": _text(safe.get("detail"), MAX_TEXT),
                "before": _text(safe.get("before_screen_id"), 40),
                "after": _text(safe.get("after_screen_id"), 40),
            }
        )
    return rows


def _outcome_pill(outcome: str) -> str:
    low = outcome.lower()
    if low in ("ok", "done", "pass") or low.endswith("_pass") or low.endswith("_ok"):
        cls = "p-ok"
    elif low in ("-", "", "skipped", "pending"):
        cls = "p-void"
    elif "fail" in low or "error" in low or "blocked" in low or "refus" in low:
        cls = "p-def"
    elif "escape" in low or "needs" in low or "ask" in low or "wait" in low:
        cls = "p-gap"
    else:
        cls = "p-void"
    return _pill(cls, outcome)


def _steps_table(rows: list) -> str:
    body = "".join(
        '<tr><td class="n">'
        + esc(row["index"], 8)
        + '</td><td class="said" dir="auto"><q>'
        + esc(row["action"], 160)
        + "</q></td>"
        + '<td class="outc">'
        + _outcome_pill(row["outcome"])
        + '</td><td class="num">'
        + esc(fmt_ms(row["ms"]), 16)
        + '</td><td class="said" dir="auto">'
        + (
            esc(row["detail"])
            if row["detail"]
            else '<span class="mt">no detail recorded</span>'
        )
        + '</td><td><span class="cap'
        + ("" if (row["before"] or row["after"]) else " none")
        + '">'
        + (esc(row["before"], 40) or "—")
        + " → "
        + (esc(row["after"], 40) or "—")
        + "</span></td></tr>"
        for row in rows
    )
    return (
        '<div class="tablewrap"><table class="cov turns"><thead><tr>'
        '<th scope="col">#</th><th scope="col">Action</th><th scope="col">Outcome</th>'
        '<th scope="col" class="num">Took</th><th scope="col">Detail</th>'
        '<th scope="col">Screen before → after</th>'
        "</tr></thead><tbody>" + body + "</tbody></table></div>"
        '<p class="hint">Took: the replay clock for that one action, as the server measured it. '
        "A type action shows what it sent, so a chat case can be read as the exchange it "
        "was — unless the value was marked secret or the field is named as a credential, "
        "in which case it is masked here and at every earlier step that wrote it down.</p>"
    )


def _seq_rows(rows: list, screens: object = None, app: str = "") -> str:
    """The run sequence: one row per action, in the order it ran.

    A row whose screen CHANGED carries the new screen's frame in its fold, so
    the sequence reads as what the tester would have seen, step by step."""
    if not rows:
        return ""
    longest = max((row["ms"] or 0) for row in rows) or 0
    clock = 0
    out = []
    for row in rows:
        kind = OP_KIND.get(row["op"].lower(), "step")
        label = "0.0s" if clock == 0 else "+%.1fs" % (clock / 1000.0)
        bar = ""
        if row["ms"] is not None and longest:
            bar = (
                '<span class="seqbar k-tool" title="'
                + str(int(row["ms"]))
                + " ms of the "
                + str(int(longest))
                + ' ms longest step in this case"><i style="width:%.1f%%"></i></span>'
                % max(3.0, min(100.0, row["ms"] / float(longest) * 100.0))
            )
        head = (
            '<span class="seqk">'
            + esc(row["op"], 24)
            + '</span><span class="seqtx" dir="auto">'
            + esc(row["action"], 160)
            + "</span>"
            + (
                ('<span class="mt">' + esc(fmt_ms(row["ms"]), 16) + "</span>")
                if row["ms"] is not None
                else ""
            )
            + _outcome_pill(row["outcome"])
        )
        detail = ""
        if row["detail"]:
            detail = '<p class="seqfull" dir="auto">' + esc(row["detail"]) + "</p>"
        if row["before"] or row["after"]:
            detail += (
                '<div class="chipnote"><i>screen</i>'
                + (esc(row["before"], 40) or "—")
                + " → "
                + (esc(row["after"], 40) or "—")
                + "</div>"
            )
        if row["after"] and row["after"] != row["before"]:
            detail += (
                '<div class="phonewide">'
                + _phone_html(
                    "Screen after",
                    "what this action left on screen",
                    row["after"],
                    screens,
                    app,
                )
                + "</div>"
            )
        if detail:
            out.append(
                '<li class="seq is-'
                + kind
                + '"><details class="seqfold"><summary><span class="chev" aria-hidden="true"></span>'
                '<span class="seqt">'
                + label
                + '</span><div class="seqmain">'
                + head
                + "</div>"
                + bar
                + '</summary><div class="seqdetail">'
                + detail
                + "</div></details></li>"
            )
        else:
            out.append(
                '<li class="seq is-'
                + kind
                + '"><span class="chev ghost" aria-hidden="true"></span><span class="seqt">'
                + label
                + '</span><div class="seqmain">'
                + head
                + "</div>"
                + bar
                + "</li>"
            )
        clock += row["ms"] or 0
    return '<ul class="seqlist">' + "".join(out) + "</ul>"


# ── one case ───────────────────────────────────────────────────────────────────


def _planned_case(manifest: dict, tc_id: str) -> dict:
    """The suite's own record of the case, when the manifest carries one."""
    for entry in manifest.get("cases") or []:
        if isinstance(entry, dict) and _text(entry.get("tc_id"), 40) == tc_id:
            return entry
    return {}


def _expected_of(planned: dict) -> str:
    parts = []
    for step in planned.get("steps") or []:
        if isinstance(step, dict):
            expected = _text(step.get("expected_result"), 160)
            if expected:
                parts.append(expected)
    return " · ".join(parts)


def _case_wall(rows: list) -> int | None:
    measured = [row["ms"] for row in rows if row["ms"] is not None]
    return sum(measured) if measured else None


def _case_facts(case: object, manifest: dict) -> dict:
    """Everything a card, a table row, a chip and a KPI need, computed ONCE."""
    raw = case if isinstance(case, dict) else {}
    safe = run_store.redact(raw)
    safe = safe if isinstance(safe, dict) else {}
    tc_id = _text(safe.get("tc_id"), 40)
    rows = _trace_rows(safe.get("trace"))
    planned = _planned_case(manifest, tc_id)
    first_before = rows[0]["before"] if rows else _text(safe.get("screen_id"), 40)
    last_after = ""
    for row in reversed(rows):
        if row["after"]:
            last_after = row["after"]
            break
    screens_seen = {row["before"] for row in rows} | {row["after"] for row in rows}
    screens_seen.discard("")
    try:
        escapes = int(safe.get("escapes") or 0)
    except (TypeError, ValueError):
        escapes = 0
    try:
        free_stops = max(0, int(safe.get("free_stops") or 0))
    except (TypeError, ValueError):
        free_stops = 0
    wall = _case_wall(rows)
    return {
        "tc_id": tc_id,
        "slug": _slug(tc_id),
        "title": _text(safe.get("title"), 120),
        "verdict": _verdict_of(safe),
        "status": _text(safe.get("status"), 40),
        "reason": _text(safe.get("reason"), 400),
        "escapes": max(0, escapes),
        "free_stops": free_stops,
        # Planning turns by the tester's own chat model: the first plan, every
        # escape-hatch re-plan, AND every uncharged stale-selector stop. A case
        # still planning has taken none yet.
        #
        # `free_stops` was written to the checkpoint by `case_runner` and read by
        # NOTHING -- not this module, not the submit reply, not `qa_mobile_status`
        # -- so a case that spent two uncharged stops and one escape reported two
        # planning turns for four round trips. The disclosure is this number,
        # which the card already showed, rather than a new column: a count that is
        # wrong is worse than one that is missing.
        "plans": (1 + max(0, escapes) + free_stops) if rows else 0,
        "updated": safe.get("updated"),
        "rows": rows,
        "first": first_before,
        "last": last_after,
        "screens": len(screens_seen),
        "wall": wall,
        "lat": _lat_bucket(wall),
        "module": _text(planned.get("module"), 60) or "(unfiled)",
        "priority": _text(planned.get("priority"), 24) or "(unset)",
        "type": _text(planned.get("type"), 24) or "(unset)",
        "expected": _expected_of(planned),
    }


def _vstrip(facts: dict) -> str:
    verdict = facts["verdict"]
    _sw, _seg, pill_cls, label = _tone(verdict)
    tone = {"pass": "v-pass", "fail": "v-fail", "unverified": "v-fail"}.get(verdict, "")
    if verdict in DONE_VERDICTS:
        why = (
            ("<b>" + esc(facts["reason"], 400) + "</b>")
            if facts["reason"]
            else "the run recorded no reason for this verdict"
        )
    else:
        why = (
            "this case has not reached a verdict — its status is <b>"
            + esc(facts["status"] or "unknown", 40)
            + "</b>, so nothing here is final yet"
        )
    return (
        '<div class="vstrip '
        + tone
        + '"><span class="label">Verdict</span>'
        + _pill(pill_cls, label.lower())
        + '<span class="vwhy">'
        + why
        + "</span></div>"
    )


def _ended_block(facts: dict) -> str:
    def row(label, value):
        return (
            '<div class="erow"><span class="elab">'
            + esc(label, 40)
            + "</span>"
            + value
            + "</div>"
        )

    rows = (
        row(
            "status",
            '<span class="cap">' + esc(facts["status"] or "unknown", 40) + "</span>",
        )
        + row(
            "verdict",
            _pill(_tone(facts["verdict"])[2], _tone(facts["verdict"])[3].lower()),
        )
        + row(
            "escape-hatch turns",
            '<span class="cap">' + str(facts["escapes"]) + "</span>",
        )
        + row(
            "last checkpoint",
            '<span class="cap">' + esc(_stamp(facts["updated"]), 40) + "</span>",
        )
        + row(
            "last screen",
            '<span class="cap'
            + ("" if facts["last"] else " none")
            + '">'
            + (esc(facts["last"], 40) or "none recorded")
            + "</span>",
        )
    )
    return (
        '<div class="cmeta"><div><h4>Ended at</h4><div class="estate">'
        + rows
        + "</div></div></div>"
    )


def _case_card(
    case: object, screens: object, manifest: dict | None = None, app: str = ""
) -> str:
    """One case card in the shell's anatomy: expected -> verdict -> screens -> steps -> sequence -> ended at."""
    facts = _case_facts(case, manifest if isinstance(manifest, dict) else {})
    return _card_html(facts, screens, app)


def _card_html(
    facts: dict, screens: object, app: str, loaded: dict | None = None
) -> str:
    verdict = facts["verdict"]
    _sw, _seg, pill_cls, label = _tone(verdict)
    rows = facts["rows"]
    first_action = rows[0]["action"] if rows else ""
    snippet = ""
    if first_action or facts["reason"]:
        snippet = (
            '<span class="csnip">'
            + (
                ('<q dir="auto">' + esc(first_action, 64) + "</q>")
                if first_action
                else ""
            )
            + (
                '<span class="arr" aria-hidden="true">→</span>'
                if first_action and facts["reason"]
                else ""
            )
            + (
                ('<q class="sara" dir="auto">' + esc(facts["reason"], 64) + "</q>")
                if facts["reason"]
                else ""
            )
            + "</span>"
        )
    bits = [_pill(pill_cls, label), MSEP, _metric(len(rows), "steps")]
    if facts["wall"] is not None:
        bits += [MSEP, _metric(fmt_ms(facts["wall"]), "wall")]
    bits += [
        MSEP,
        _metric(facts["plans"], "LLM turns"),
        _metric(facts["escapes"], "escape hatch"),
        _metric(facts["screens"], "screens"),
    ]
    if loaded:
        # The app's side of the same case: LLM / API / tool counts, tokens, cost.
        bits += ev_render.card_metrics(loaded, facts["tc_id"])
    search = " ".join(
        [
            facts["tc_id"],
            facts["title"],
            facts["module"],
            facts["priority"],
            facts["type"],
            verdict,
            facts["reason"],
        ]
        + [row["action"] for row in rows]
        + [row["detail"] for row in rows]
    ).lower()[:4000]
    data = {
        "data-tc": facts["tc_id"],
        "data-verdict": verdict,
        "data-module": facts["module"],
        "data-priority": facts["priority"],
        "data-type": facts["type"],
        "data-steps": str(len(rows)),
        "data-wall": str(facts["wall"]) if facts["wall"] is not None else "",
        "data-escapes": str(facts["escapes"]),
        "data-plans": str(facts["plans"]),
        "data-lat": facts["lat"],
        "data-search": search,
    }
    if loaded:
        data.update(ev_render.card_data(loaded, facts["tc_id"]))
    attrs = " ".join(
        name + '="' + esc(value, 4000) + '"'
        for name, value in data.items()
        if value not in (None, "")
    )
    expected = (
        esc(facts["expected"], 600)
        if facts["expected"]
        else "the suite states no expected result for this case"
    )
    # ONE phone when the case began and ended on the same screen: two identical
    # frames side by side read as a diff with nothing in it.
    if facts["first"] and facts["first"] == facts["last"]:
        frames = _phone_html(
            "On screen",
            "the case began and ended on this screen",
            facts["first"],
            screens,
            app,
        )
    else:
        frames = _phone_html(
            "First screen", "the screen the case began on", facts["first"], screens, app
        ) + _phone_html(
            "Last screen", "the screen the case ended on", facts["last"], screens, app
        )
    phones = (
        '<div class="phonewide"><div class="phonepair">'
        + frames
        + "</div>"
        + '<p class="hint">Every screen here is composed from the screen\'s element list — the '
        "text, labels and rectangles the server already held — drawn the way the app lays them "
        "out; no screenshot is ever taken of the emulator, by design, so fonts, colours and "
        "icons are not the app's own. The element map under each phone holds the exact "
        "rectangles. A phone that says the screen was not captured is a step whose screen the "
        "run did not store — not a step that did not happen.</p></div>"
    )
    # What the app heard and answered inside this case's window (plan P3).
    turns = ev_render.turns_table(loaded, facts["tc_id"]) if loaded else ""
    steps = (
        _sec_block(
            "Steps",
            _steps_table(rows),
            count=len(rows),
            note="every action replayed, in order",
            open_=True,
        )
        if rows
        else _sec_block(
            "Steps",
            '<p class="empty-note big">No step has been replayed for this case yet.</p>',
            note="nothing replayed yet",
            open_=True,
        )
    )
    # The merged stream: the app's records and the lane's actions on one clock,
    # when the case has app evidence; the lane's own rows otherwise.
    merged = (
        ev_exchanges.seqlist(ev_render.sequence_items(loaded, facts["tc_id"], rows))
        if loaded
        else ""
    )
    sequence = _sec_block(
        "Run sequence",
        merged
        or _seq_rows(rows, screens, app)
        or '<p class="empty-note big">No step has been replayed for this case yet.</p>',
        count=len(rows) if rows else None,
        note="everything in the order it ran"
        + (
            " — the app's records and the lane's actions on one clock" if merged else ""
        ),
        open_=True,
    )
    return (
        '\n<details class="case rail-'
        + RAIL.get(verdict, "none")
        + '" id="case-'
        + esc(facts["slug"], 40)
        + '" '
        + attrs
        + '>\n  <summary>\n    <span class="cid">'
        + esc(facts["tc_id"], 40)
        + '</span>\n    <span class="cuse"><span class="cuc" dir="auto">'
        + (esc(facts["title"], 120) or "(untitled case)")
        + '</span><span class="carea">'
        + esc(facts["module"], 60)
        + " · "
        + esc(facts["priority"], 24)
        + " · "
        + esc(facts["type"], 24)
        + "</span>"
        + snippet
        + '</span>\n    <span class="cstate">'
        + _pill(pill_cls, label)
        + '</span>\n    <a class="plink" href="#case-'
        + esc(facts["slug"], 40)
        + '" title="Copy a link to this record">#</a>\n    <div class="smetrics">'
        + "".join(bits)
        + '</div>\n  </summary>\n  <div class="cbody"><p class="cexp"><b>Expected</b> — '
        + expected
        + "</p>"
        + _vstrip(facts)
        + phones
        + turns
        + steps
        + sequence
        + _ended_block(facts)
        + "</div>\n</details>"
    )


# ── the sections ───────────────────────────────────────────────────────────────


def _app_label(manifest: dict) -> str:
    package = _text(manifest.get("package"), 80)
    return package or "(no app)"


def _facts_strip(
    run_id: str,
    manifest: dict,
    lease: dict,
    partial: bool,
    loaded: dict | None = None,
) -> str:
    holder = _text(lease.get("session_id"), 40)
    displaced = _text(lease.get("taken_over_from"), 40)
    items = [
        (
            "device",
            esc(manifest.get("serial") or "(not attached)", 40),
            esc(manifest.get("avd") or "(unknown avd)", 40) + " · emulator",
            "",
        ),
        ("app", esc(_app_label(manifest), 80), "the package under test", ""),
        (
            "lane",
            esc(manifest.get("lane") or "suite", 24),
            "started from " + esc(manifest.get("source") or "(unrecorded)", 40),
            "",
        ),
        (
            "created",
            esc(_stamp(manifest.get("created")), 40),
            "run " + esc(run_id, 64),
            "",
        ),
        (
            "state",
            "in progress" if partial else "finished",
            "some cases have no verdict yet"
            if partial
            else "every planned case reached a verdict",
            "gap" if partial else "ok",
        ),
    ]
    if loaded:
        items.extend(ev_render.facts_cells(loaded))
    if holder:
        items.append(
            (
                "lease",
                "held by session " + esc(holder, 40),
                "heartbeat " + esc(_stamp(lease.get("heartbeat")), 40),
                "",
            )
        )
    if displaced:
        items.append(
            (
                "taken over from",
                esc(displaced, 40),
                "an earlier chat that was told to stop",
                "",
            )
        )
    cells = "".join(
        '<div class="rf"><span class="rfl">'
        + label
        + '</span><span class="rfv">'
        + (('<i class="sw ' + tone + '"></i>') if tone else "")
        + value
        + (("<small>" + sub + "</small>") if sub else "")
        + "</span></div>"
        for label, value, sub, tone in items
    )
    return '<div class="runstrip" aria-label="Run facts">' + cells + "</div>"


def _segbar(counts: dict) -> str:
    total = sum(int(n) for n in counts.values()) or 1
    ordered = [(name, int(counts.get(name) or 0)) for name in TILES]
    ordered += [
        (name, int(n)) for name, n in sorted(counts.items()) if name not in TILES
    ]
    segs, legend = [], []
    for name, n in ordered:
        if not n:
            continue
        sw, seg, _pill_cls, label = _tone(name)
        pct = n / total * 100
        if pct >= (len(label) + 3) * 1.6:
            text = "<b>" + str(n) + "</b>" + esc(label.lower(), 40)
        elif pct >= 5:
            text = "<b>" + str(n) + "</b>"
        else:
            text = ""
        segs.append(
            '<span class="'
            + seg
            + '" style="flex:'
            + str(n)
            + '" title="'
            + str(n)
            + " "
            + esc(label.lower(), 40)
            + '">'
            + text
            + "</span>"
        )
        legend.append(
            '<li><button type="button" data-jump-group="verdict" data-jump-value="'
            + esc(name, 24)
            + '"><i class="sw '
            + sw
            + '"></i><b>'
            + str(n)
            + "</b>"
            + esc(label, 40)
            + "</button></li>"
        )
    return (
        '<figure class="seg"><figcaption><b>Outcomes</b><span>what each case came to</span></figcaption>'
        '<div class="segbar">'
        + "".join(segs)
        + '</div><ul class="seglegend">'
        + "".join(legend)
        + "</ul></figure>"
    )


def _summary_html(tally: dict, total: int) -> str:
    """The machine-readable totals the selfcheck compares, wrapping the outcome tiles."""
    ordered = [(name, int(tally.get(name) or 0)) for name in TILES]
    ordered += [
        (name, int(count)) for name, count in sorted(tally.items()) if name not in TILES
    ]
    attrs = "".join(
        " data-" + name.replace("_", "-") + '="' + str(int(count)) + '"'
        for name, count in ordered
    )
    hero = _kpi(
        str(int(total)),
        "cases checkpointed",
        (
            str(int(tally.get("pass") or 0))
            + " passed · "
            + str(int(tally.get("fail") or 0))
            + " failed · "
            + str(int(tally.get("blocked") or 0))
            + ' blocked · <a href="#cases">all cases →</a>'
        ),
        hero=True,
    )
    return (
        '<div id="'
        + SUMMARY_ID
        + '" data-total="'
        + str(int(total))
        + '"'
        + attrs
        + '><div class="outcome">'
        + hero
        + _segbar(tally)
        + "</div></div>"
    )


def _overview(
    facts: list,
    tally: dict,
    manifest: dict,
    partial: bool,
    screens: object = None,
    app: str = "",
    loaded: dict | None = None,
) -> str:
    steps = sum(len(f["rows"]) for f in facts)
    walls = [f["wall"] for f in facts if f["wall"] is not None]
    step_ms = [row["ms"] for f in facts for row in f["rows"] if row["ms"] is not None]
    escapes = sum(f["escapes"] for f in facts)
    plans = sum(f["plans"] for f in facts)
    # NOT `screens`: that name is the screen LIBRARY parameter, and a count
    # bound to it made every opening frame below render as not captured.
    captured = sum(f["screens"] for f in facts)
    planned = 0
    try:
        planned = int(manifest.get("total") or 0)
    except (TypeError, ValueError):
        planned = 0
    run_tiles = [
        _kpi(
            str(steps),
            "actions replayed",
            "across "
            + str(len(facts))
            + " case card"
            + ("" if len(facts) == 1 else "s"),
        ),
        _kpi(
            esc(fmt_ms(sum(walls)), 16) if walls else '<span class="kv-gap">n/a</span>',
            "replay time",
            "the sum of every measured action"
            if walls
            else "no action carried a measured duration",
        ),
        _kpi(
            esc(fmt_ms(max(step_ms)), 16)
            if step_ms
            else '<span class="kv-gap">n/a</span>',
            "slowest action",
            'the worst single action in the run · <a href="#perf">timings →</a>'
            if step_ms
            else "nothing was timed",
        ),
        _kpi(
            str(plans),
            "LLM turns",
            "planning turns by the tester\u2019s own chat model \u2014 one plan per case plus "
            "every escape-hatch re-plan, and every stop the server did not charge "
            "as an escape. Tokens and cost are not shown: no model call "
            "passes through this server, so it has nothing to meter",
        ),
        _kpi(
            str(escapes),
            "escape-hatch turns",
            "times the model was asked for a plan mid-case"
            if escapes
            else "every case ran its script through",
            tone="c-gap" if escapes else "",
        ),
        _kpi(
            str(captured),
            "screens captured",
            "distinct pruned screens referenced by the steps",
        ),
        _kpi(
            str(planned),
            "cases planned",
            (
                "in progress — "
                + str(max(0, planned - len(facts)))
                + " not yet checkpointed"
            )
            if partial
            else "every planned case reached a verdict",
            tone="c-gap" if partial else "c-pass",
        ),
    ]
    note = (
        '<details class="note fold"><summary><h3>What this report can and cannot tell you</h3>'
        '<span class="mt">how to read every figure above</span></summary><div class="notebody">'
        "<p>A case is <b>passed</b> when the model that planned it said so after the last action it "
        "asked for; <b>failed</b> and <b>blocked</b> likewise. The server replays actions and reports "
        "outcomes; it never judges a screen itself. A case in flight shows its status, never a verdict.</p>"
        "<p><b>LLM turns</b> counts the planning turns the tester\u2019s own chat model took: one "
        "plan per case, every escape-hatch re-plan, and every stop the server did not charge "
        "as an escape -- so it can exceed the <b>escapes</b> figure beside it, and the "
        "difference is the stops this run was given for free. "
        "There is no token or cost figure because "
        "no model call passes through this server \u2014 the model runs in the tester\u2019s chat, "
        "and this page can only count the plans it was handed.</p>"
        "<p>Every screen is <b>composed</b> from the element list the server already held — text, labels and bounds. "
        "No screenshot is ever taken of the emulator, by design, so a frame shows where controls "
        "were and what they said — not how they looked.</p>"
        "<p>Credential values are masked: the run store writes <code>***</code> for a marked value "
        "and for a credential-named key, and this page masks again and never prints an action's "
        "typed text. It cannot mask a value written under an unrecognised key with no marker; that "
        "limit is the run store's, and it is stated rather than papered over.</p></div></details>"
    )
    # The reference's "screen every case opened on": the first screen the run
    # saw, drawn once, with how many distinct opening screens there were.
    opening = ""
    first_ids = [f["first"] for f in facts if f["first"]]
    if first_ids:
        distinct = len(set(first_ids))
        opening = _sec_block(
            "The screen every case opened on"
            if distinct == 1
            else "The screen the first case opened on",
            '<div class="phonewide">'
            + _phone_html(
                "Opening screen", "as the run first saw it", first_ids[0], screens, app
            )
            + "</div>",
            count=distinct,
            note=(
                "one opening screen, shared by every case"
                if distinct == 1
                else str(distinct) + " distinct opening screens across the cases"
            ),
        )
    return (
        _summary_html(tally, len(facts))
        + '<div class="kpis run">'
        + "".join(run_tiles)
        + ("".join(ev_render.overview_tiles(loaded)) if loaded else "")
        + "</div>"
        + opening
        + (
            (ev_render.session_block(loaded) + ev_render.trust_block(loaded))
            if loaded
            else ""
        )
        + note
    )


def _coverage(facts: list) -> str:
    if not facts:
        return '<p class="empty-note big">No case has been checkpointed yet.</p>'
    rows = ""
    for f in facts:
        _sw, _seg, pill_cls, label = _tone(f["verdict"])
        rows += (
            '<tr class="jumpable" data-jump-case="'
            + esc(f["slug"], 40)
            + '" tabindex="0" data-stc="'
            + esc(f["tc_id"], 40)
            + '" data-stitle="'
            + esc(f["title"], 120)
            + '" data-smodule="'
            + esc(f["module"], 60)
            + '" data-sverdict="'
            + esc(f["verdict"], 24)
            + '" data-ssteps="'
            + str(len(f["rows"]))
            + '" data-swall="'
            + (str(f["wall"]) if f["wall"] is not None else "-1")
            + '"><td class="uc">'
            + esc(f["tc_id"], 40)
            + "<small>"
            + esc(f["module"], 60)
            + '</small></td><td dir="auto">'
            + (esc(f["title"], 120) or "(untitled case)")
            + "</td><td>"
            + _pill(pill_cls, label)
            + '</td><td class="num">'
            + str(len(f["rows"]))
            + '</td><td class="num">'
            + esc(fmt_ms(f["wall"]), 16)
            + '</td><td><span class="goto">open →</span></td></tr>'
        )
    return (
        '<div class="tablewrap"><table class="cov" id="covtable"><thead><tr>'
        '<th data-sort="tc" scope="col">Case</th><th data-sort="title" scope="col">Title</th>'
        '<th data-sort="verdict" scope="col">Verdict</th><th data-sort="steps" scope="col" class="num">Actions</th>'
        '<th data-sort="wall" scope="col" class="num">Replay time</th>'
        '<th scope="col"><span class="visually-hidden">Open case</span></th>'
        "</tr></thead><tbody>" + rows + "</tbody></table></div>"
    )


def _perf(facts: list, loaded: dict | None = None) -> str:
    step_ms = [row["ms"] for f in facts for row in f["rows"] if row["ms"] is not None]
    walls = [(f, f["wall"]) for f in facts if f["wall"] is not None]
    if not step_ms:
        return (
            '<p class="empty-note big">Nothing here carried a measured duration yet — the tiles '
            "and the figure appear once an action has been replayed.</p>"
        ) + (ev_render.perf_extra(loaded) if loaded else "")
    tiles = [
        _kpi(
            esc(fmt_ms(_percentile(step_ms, 0.5)), 16),
            "How long an action took",
            '<span class="kstat">usually '
            + esc(fmt_ms(_percentile(step_ms, 0.5)), 16)
            + ", between "
            + esc(fmt_ms(min(step_ms)), 16)
            + " and "
            + esc(fmt_ms(max(step_ms)), 16)
            + '</span><span class="kstat">across '
            + str(len(step_ms))
            + " actions</span>"
            '<details class="kwhy"><summary>what this means</summary><p>measured by the replay '
            "clock around one action — the tap or the wait itself, not the model's planning turn, "
            "which happens in the tester's own chat</p></details>",
        ),
    ]
    if walls:
        wall_values = [w for _f, w in walls]
        tiles.append(
            _kpi(
                esc(fmt_ms(_percentile(wall_values, 0.5)), 16),
                "How long a case took",
                '<span class="kstat">usually '
                + esc(fmt_ms(_percentile(wall_values, 0.5)), 16)
                + ", between "
                + esc(fmt_ms(min(wall_values)), 16)
                + " and "
                + esc(fmt_ms(max(wall_values)), 16)
                + '</span><span class="kstat">across '
                + str(len(wall_values))
                + " cases</span>"
                '<details class="kwhy"><summary>what this means</summary><p>the sum of that case\'s '
                "measured actions, a FLOOR rather than the whole truth: the turns the model spent "
                "planning between actions are nobody's measurement here</p></details>",
            )
        )
    figure = ""
    if walls:
        top = max(w for _f, w in walls) or 1
        ordered = sorted(walls, key=lambda pair: -pair[1])
        items = "".join(
            '<li class="runrow"><button type="button" data-jump-case="'
            + esc(f["slug"], 40)
            + '"><span class="rname"><b>'
            + esc(f["tc_id"], 40)
            + "</b>"
            + (esc(f["title"], 80) or "(untitled case)")
            + '</span><span class="rval">'
            + esc(fmt_ms(w), 16)
            + '</span><span class="rtrack"><i class="sw-lat-'
            + (f["lat"] or "fast")
            + '" style="width:%.1f%%"></i></span></button></li>'
            % max(1.0, w / float(top) * 100.0)
            for f, w in ordered
        )
        buckets = Counter(f["lat"] for f, _w in walls)
        legend = "".join(
            '<li><button type="button" data-jump-group="lat" data-jump-value="'
            + key
            + '"><i class="sw sw-lat-'
            + key
            + '"></i><b>'
            + str(buckets.get(key, 0))
            + "</b>"
            + label
            + "</button></li>"
            for key, label, _ceiling in LAT_BUCKETS
        )
        figure = (
            '<figure class="hist"><figcaption><b>How long a case took</b><span>'
            + str(len(walls))
            + " case"
            + ("" if len(walls) == 1 else "s")
            + ', each one shown · click one to open it</span></figcaption><ul class="runs">'
            + items
            + '</ul><ul class="seglegend">'
            + legend
            + "</ul></figure>"
        )
    return (
        '<p class="hint">Every action the server replays is timed on its own clock, so these are '
        "real elapsed times — for the action alone. The model's planning between actions runs in "
        "the tester's chat and is not measured here.</p>"
        '<div class="kpis">'
        + "".join(tiles)
        + "</div>"
        + figure
        + (ev_render.perf_extra(loaded) if loaded else "")
    )


def _toolbar(facts: list, loaded: dict | None = None) -> str:
    verdicts = Counter(f["verdict"] for f in facts)
    modules = Counter(f["module"] for f in facts)
    priorities = Counter(f["priority"] for f in facts)
    types = Counter(f["type"] for f in facts)
    lats = Counter(f["lat"] for f in facts if f["lat"])
    ordered_verdicts = [name for name in TILES if verdicts.get(name)] + sorted(
        name for name in verdicts if name not in TILES
    )
    groups = [
        (
            "Verdict",
            _all_chip("area", len(facts))
            + "".join(
                _chip("verdict", name, _tone(name)[3], verdicts[name], _tone(name)[0])
                for name in ordered_verdicts
            ),
        ),
        (
            "Module",
            "".join(
                _chip("module", name, name, n) for name, n in modules.most_common()
            ),
        ),
        (
            "Priority",
            "".join(
                _chip("priority", name, name, n) for name, n in priorities.most_common()
            ),
        ),
        (
            "Type",
            "".join(_chip("type", name, name, n) for name, n in types.most_common()),
        ),
        (
            "Latency",
            "".join(
                _chip("lat", key, label, lats[key], "lat-" + key)
                for key, label, _c in LAT_BUCKETS
                if lats.get(key)
            ),
        ),
    ]
    if loaded:
        groups += ev_render.toolbar_groups(loaded, facts)
    sorts = (
        ("order", "case order"),
        ("steps", "actions, most first"),
        ("wall", "replay time, longest first"),
        ("plans", "LLM turns, most first"),
        ("escapes", "escape-hatch turns, most first"),
    ) + (tuple(ev_render.extra_sorts(loaded)) if loaded else ())
    opts = "".join(
        '<option value="' + k + '">' + label + "</option>" for k, label in sorts
    )
    fgs = "".join(_filter_group(label, chips) for label, chips in groups)
    total = str(len(facts))
    return (
        '\n    <div class="toolbar" id="toolbar">\n      <div class="tb-row">\n        <label class="search">\n'
        '          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><circle cx="7" cy="7" r="4.5"/><path d="m10.5 10.5 3 3"/></svg>\n'
        '          <input id="q" type="search" placeholder="Search id, title, module, actions, reasons…" autocomplete="off" spellcheck="false" aria-label="Search cases">\n'
        '          <button class="x" id="qx" type="button" aria-label="Clear search" hidden>×</button>\n'
        "          <kbd>/</kbd>\n        </label>\n"
        '        <span class="tb-count"><b id="shown">'
        + total
        + "</b> of "
        + total
        + "</span>\n"
        '        <span class="active-uc" id="active-uc" hidden><b></b><button type="button" aria-label="Clear use case">×</button></span>\n'
        '        <button type="button" class="chip" id="fbtn" aria-expanded="false" title="Show the filter groups">Filters<i id="fcount">0</i></button>\n'
        '        <label class="selectwrap">Sort\n          <select id="sort">'
        + opts
        + "</select>\n        </label>\n"
        '        <span class="bulk">\n          <button type="button" class="chip" id="expand-all">Expand shown</button>\n'
        '          <button type="button" class="chip" id="collapse-all">Collapse</button>\n        </span>\n      </div>\n'
        '      <div class="tb-row fgroups">\n        ' + fgs + "\n"
        '        <button type="button" class="chip ghost" id="clear" data-clear hidden>Clear filters</button>\n'
        "      </div>\n    </div>"
    )


def _cases_section(
    facts: list, screens: object, app: str, loaded: dict | None = None
) -> str:
    cards = "".join(_card_html(f, screens, app, loaded) for f in facts)
    done = sum(1 for f in facts if f["verdict"] in DONE_VERDICTS)
    lede = (
        str(len(facts))
        + " case"
        + ("" if len(facts) == 1 else "s")
        + " checkpointed, "
        + str(done)
        + " of them with a verdict. Open a case for its screens, every action and the reason it "
        "ended the way it did. Press <kbd>/</kbd> to search."
    )
    body = (
        _toolbar(facts, loaded)
        + '\n<div class="cases" id="caselist">'
        + (
            cards
            or '\n<p class="empty-note big">No case has been checkpointed yet.</p>'
        )
        + '</div>\n    <div class="noresults" id="noresults" hidden>\n      No cases match these filters.\n'
        '      <div><button class="chip ghost" type="button" data-clear>Clear filters</button></div>\n    </div>'
    )
    return _sechead("cases", "Every case", "what ran, and what it came to", lede, body)


def _footer_html(run_id: str, manifest: dict) -> str:
    return (
        "Runner: <code>tools/mobile/case_runner.py</code> · Run: <code>"
        + esc(run_id, 64)
        + "</code> · Driven against <code>"
        + esc(manifest.get("serial") or "(not attached)", 40)
        + "</code><br>Rendered "
        + esc(_short_stamp(time.time()), 40)
        + " from <code>manifest.json</code>, <code>cases/*.json</code> and <code>screens/*.json</code> "
        "in the run's own directory by <code>tools/mobile/report.py</code> on the shared shell in "
        "<code>tools/mobile/report_shell.html</code>. This is one self-contained page: it references "
        "nothing but its typefaces, and it opens offline in the system face."
    )


def _fill(template: str, slots: dict) -> str:
    """Every ``{{SLOT}}`` in ONE pass, so a filled value is never itself expanded."""

    def one(match):
        return slots[match.group(1)]

    return _SLOT.sub(one, template)


def _findings_section(manifest: dict, turns: int) -> str:
    """What an exploratory run FOUND. ``""`` for any other lane.

    ``explore_runner.apply_turn_result`` has always accumulated
    ``manifest["explore"]["findings"]`` and NOTHING read it -- not this module,
    not the shell -- so a 20-minute session's entire output was invisible.

    Two honesty rules this section keeps, both of which the page already
    applies elsewhere:

    * an explore run with no ``explore.stop`` is PARTIAL (``_is_partial``), so
      the reader is told the list is incomplete rather than shown a conclusion;
    * a turn that recorded NO finding is counted out loud. The packet asks for
      one every turn, so silence is a gap in the evidence rather than a turn
      with nothing to report, and a reader who is not told cannot tell the two
      apart.

    Every interpolated value goes through :func:`esc`, and the table carries no
    ``id`` and no ``data-sort`` -- both are wired to the shell's own script.
    """
    body = manifest if isinstance(manifest, dict) else {}
    if str(body.get("lane") or "") != "explore":
        return ""
    explore = body.get("explore")
    explore = explore if isinstance(explore, dict) else {}
    notes = [
        note for note in list(explore.get("findings") or []) if isinstance(note, dict)
    ]
    try:
        replayed = max(0, int(turns or 0))
    except (TypeError, ValueError):
        replayed = 0
    # Counted by DISTINCT turn, never by row. A turn resubmitted with a finding
    # appends a second row while leaving ONE checkpoint, so a row count read
    # "2 findings over 1 turn" and drove the silent count negative into its own
    # clamp -- which hid the miscount instead of reporting it.
    spoke = set()
    for note in notes:
        try:
            spoke.add(int(note.get("turn") or 0))
        except (TypeError, ValueError):
            continue
    silent = max(0, replayed - len(spoke))
    stop = _text(explore.get("stop"), 40)
    lede = (
        "Goal: "
        + (esc(explore.get("goal"), 400) or "(not recorded)")
        + ". "
        + str(len(spoke))
        + " finding"
        + ("" if len(spoke) == 1 else "s")
        + " recorded over "
        + str(replayed)
        + " turn"
        + ("" if replayed == 1 else "s")
        + (
            ", and "
            + str(silent)
            + " turn"
            + ("" if silent == 1 else "s")
            + " recorded none \u2014 every turn is asked for one, so those are "
            "gaps in the evidence rather than turns with nothing to report"
            if silent
            else ""
        )
        + ". "
        + (
            "This session ended: " + esc(stop, 40) + "."
            if stop
            else "This session has NOT ended, so this list is incomplete."
        )
    )
    rows = "".join(
        '<tr><td class="num">'
        + esc(note.get("turn"), 8)
        + '</td><td dir="auto">'
        + esc(note.get("note"), 600)
        + "</td></tr>"
        for note in notes
    )
    table = (
        '<div class="tablewrap"><table class="cov"><thead><tr>'
        '<th scope="col" class="num">Turn</th>'
        '<th scope="col">What the turn reported</th>'
        "</tr></thead><tbody>" + rows + "</tbody></table></div>"
        if rows
        else '<p class="empty-note big">No turn recorded a finding.</p>'
    )
    return _sechead(
        "findings", "What exploring found", "one line per turn", lede, table
    )


def _document(
    *,
    run_id: str,
    manifest: dict,
    cases: list,
    screens: object,
    lease: dict,
    tally: dict,
    partial: bool,
) -> str:
    facts = [_case_facts(case, manifest) for case in cases]
    app = _app_label(manifest)
    # The app's side of the run (plan P3): read ONCE, joined to the cases, and
    # handed to every section. Never raises; a run without a capture, or a
    # package without a profile, renders every evidence fragment as a stated gap.
    loaded = ev_render.load_run_evidence(
        run_id, manifest, cases, ev_profiles.profile_for(manifest.get("package"))
    )
    steps = sum(len(f["rows"]) for f in facts)
    title = "Mobile run " + str(run_id)
    # Built ONCE and reused: the nav must not offer a section this page does
    # not emit, and the findings section exists only for the explore lane.
    findings = _findings_section(manifest, len(facts))
    nav_items = [
        ("overview", "Overview"),
        ("coverage", "Coverage"),
        ("apis", "APIs"),
        ("perf", "Performance"),
    ]
    if findings:
        nav_items.append(("findings", "Findings"))
    nav_items.append(("cases", "Cases"))
    nav = "".join(
        '<a href="#' + sid + '" data-nav="' + sid + '">' + label + "</a>"
        for sid, label in nav_items
    )
    meta = "".join(
        "<span><b>" + k + "</b> " + v + "</span>"
        for k, v in (
            (
                "Suite",
                str(len(facts))
                + " case"
                + ("" if len(facts) == 1 else "s")
                + " checkpointed · "
                + esc(manifest.get("total") or 0, 12)
                + " planned",
            ),
            (
                "This run",
                str(steps)
                + " action"
                + ("" if steps == 1 else "s")
                + " replayed · rendered "
                + esc(_short_stamp(time.time()), 40),
            ),
            (
                "Driven against",
                esc(app, 80)
                + " on "
                + esc(manifest.get("serial") or "(not attached)", 40),
            ),
            (
                "State",
                "in progress — some cases have no verdict yet"
                if partial
                else "finished — every planned case reached a verdict",
            ),
        )
    )
    sections = (
        _sechead(
            "overview",
            "At a glance",
            "the run in numbers",
            "Every figure here is about what the emulator DID with a case. Whether the app is right is the tester's question, not this one.",
            _overview(facts, tally, manifest, partial, screens, app, loaded),
        )
        + _sechead(
            "coverage",
            "Coverage by case",
            "how far each one got",
            "Every checkpointed case and where it stands. Sort any column; open a row for its card.",
            _coverage(facts),
        )
        + _sechead(
            "apis",
            "API surface",
            "every endpoint the app reached, on the real wire",
            "Every endpoint the app called from inside a case, against the real backend rather than a fixture.",
            ev_render.apis_section(loaded),
        )
        + _sechead(
            "perf",
            "Performance",
            "what the replay could time",
            "How long things took, from the clock around each replayed action — and, when the app's own log was captured, from the moments the app wrote down itself.",
            _perf(facts, loaded),
        )
        + findings
        + _cases_section(facts, screens, app, loaded)
        + '<div id="'
        + END_ID
        + '" data-cards="'
        + str(len(cases))
        + '"></div>'
    )
    digest = hashlib.sha1(
        (
            str(run_id)
            + ":"
            + str(manifest.get("created") or "")
            + ":"
            + str(len(cases))
        ).encode("utf-8")
    ).hexdigest()[:12]
    try:
        body = _fill(
            SHELL,
            {
                "DOC_TITLE": esc(title, 120),
                "BRAND": "Mobile Run",
                "BRAND_SUB": "QA Agents",
                "NAV": nav,
                "EYEBROW": "QA Agents · mobile lane · one run on the emulator",
                "TITLE": esc(app, 80) + " on device, one run end to end",
                "STANDFIRST": (
                    "Every planned case replayed on the emulator from the screen it saw, one action at a "
                    "time, and on the same card what the run did underneath: every tap, every field, every "
                    "assertion and the screen before and after."
                ),
                "META": meta,
                "FACTS": _facts_strip(run_id, manifest, lease, partial, loaded),
                "SECTIONS": sections,
                "FOOTER_META": _footer_html(run_id, manifest),
                "SOURCE_STAMP": esc(str(run_id) + ":" + digest, 80),
                # Never used by this lane: nothing here is big enough to park.
                "STORES": "",
            },
        )
    finally:
        # The learned-value net stays armed only while the page is being built --
        # and not a moment longer when the build raises.
        ev_render.release()
    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head><body>\n'
        + body
        + "\n</body></html>\n"
    )


def _write_page(target: Path, page: str) -> dict:
    """tmp + ``os.replace``, so a killed render leaves the previous good file."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(page)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, target)
        return {"error": None, "content": {"path": str(target)}}
    except OSError as exc:
        logger.warning("mobile.report: could not write %s: %s", target, exc)
        return {
            "error": "Could not write the report: " + str(exc)[:200],
            "content": None,
        }


def _is_partial(manifest: dict, cases: list, tally: dict) -> bool:
    """Whether this run is still going, decided from the FILES.

    The manifest's ``cases`` key is never consulted -- a run planned by an older
    build may not have one, and the checkpoints are the authority anyway.
    """
    if str(manifest.get("lane") or "") == "explore":
        explore = manifest.get("explore")
        explore = explore if isinstance(explore, dict) else {}
        return not bool(explore.get("stop"))
    done = sum(int(count) for name, count in tally.items() if name in DONE_VERDICTS)
    planned = 0
    try:
        planned = int(manifest.get("total") or 0)
    except (TypeError, ValueError):
        planned = 0
    return bool(not cases or done < max(planned, len(cases)))


def render(run_id: str) -> dict:
    """Write ``runs/<run_id>/report.html`` from that run's files only.

    ``{"error", "content": {"path", "partial", "cards", "totals", "bytes"}}``.
    Never raises.
    """
    try:
        if not settings.qa_mobile_run_enabled:
            return {"error": FLAG_REFUSAL, "content": None}
        if not run_store.valid_run_id(run_id):
            return {
                "error": "Refusing " + repr(str(run_id)[:40]) + " as a run id.",
                "content": None,
            }
        manifest = (run_store.read_manifest(run_id) or {}).get("content")
        manifest = manifest if isinstance(manifest, dict) else {}
        if not manifest:
            return {
                "error": (
                    "No run `"
                    + _text(run_id, 64)
                    + "` on this machine, so there is nothing to report."
                ),
                "content": None,
            }
        cases = [
            case
            for case in (run_store.list_cases(run_id) or {}).get("content") or []
            if isinstance(case, dict)
        ]
        screens = (run_store.list_screens(run_id) or {}).get("content") or {}
        lease = (run_store.read_lease(run_id) or {}).get("content") or {}
        lease = lease if isinstance(lease, dict) else {}
        # NOT `tally = tally(cases)`: that binds `tally` as a local for the
        # WHOLE function body, so the call on the right resolves to an unbound
        # local and EVERY invocation raises UnboundLocalError -- which this
        # function's own `except Exception` then reports as a handled error,
        # so the module looks alive and returns nothing. Found by EXECUTING.
        counts = tally(cases)
        partial = _is_partial(manifest, cases, counts)
        page = _document(
            run_id=str(run_id),
            manifest=manifest,
            cases=cases,
            screens=screens,
            lease=lease,
            tally=counts,
            partial=partial,
        )
        target = paths.run_dir(str(run_id)) / REPORT_FILE
        written = _write_page(target, page)
        if written.get("error"):
            return written
        return {
            "error": None,
            "content": {
                "path": str(target),
                "partial": partial,
                "cards": len(cases),
                "totals": counts,
                "bytes": len(page.encode("utf-8")),
            },
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.report.render failed")
        return {"error": str(exc), "content": None}
