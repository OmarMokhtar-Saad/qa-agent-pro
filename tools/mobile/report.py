"""The standalone HTML run report, built from a run's own files and nothing else.

``render(run_id)`` reads ``runs/<run_id>/manifest.json``, ``cases/TC-*.json``,
``screens/<screen_id>.json`` and ``lease.json`` -- and NOTHING else. No device,
no ``suite_store``, no in-memory state left by a previous call. That is what
makes a MID-RUN report possible: a half-finished run is simply a run whose
checkpoints are not all terminal yet, and the page says so (``partial``).

**Every string in here was chosen by an app on the device.** A dump's ``text``,
``content-desc`` and ``resource-id`` are attacker-influenced, and this is an
HTML document, so the exposure is XSS in the tester's browser rather than only
prompt spoofing. ``perception._clean`` caps length and neutralises the guard
markers; it does NOT escape HTML. Three layers answer that, in this order:

1. **There is no ``script`` element in the document and no event-handler
   attribute is ever emitted.** The report is static; nothing here needs
   JavaScript. So that injection CONTEXT does not exist rather than being
   defended.
2. **The one ``style`` element's text is :data:`_CSS`, with no interpolation of
   any kind** -- no f-string, no ``%``, no ``.format``. The CSS context is
   likewise absent by construction, and a test asserts the page's style text
   equals the constant, so any attacker byte reaching it makes the two differ.
3. **Every interpolated value goes through :func:`esc`** =
   ``html.escape(_text(value), quote=True)``. ``quote=True`` is load-bearing:
   attribute values are quoted and ``&quot;``/``&#x27;`` is what stops a value
   closing its own attribute.

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

import html
import logging
import os
import re
import time
from pathlib import Path

from config.settings import settings
from tools.mobile import paths, run_store

logger = logging.getLogger(__name__)

REPORT_FILE = "report.html"

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
DONE_VERDICTS = ("pass", "fail", "blocked")

#: Tiles always rendered, in this order, so a zero is visible rather than absent.
TILES = (
    "pass",
    "fail",
    "blocked",
    "needs_tester",
    "needs_model",
    "planning",
    "unknown",
)

FLAG_NAME = "QA_MOBILE_RUN_ENABLED"

FLAG_REFUSAL = (
    "Nothing was written. Building a mobile run report needs `"
    + FLAG_NAME
    + "=true` in `.env` and an MCP server restart (quit and reopen the editor)."
)

NOT_CAPTURED = "this screen was not captured"

_SAFE_TOKEN = re.compile(r"[^a-z0-9_]+")

#: No interpolation. Ever. See layer 2 in the module docstring.
_CSS = """
:root { color-scheme: light dark; --bg:#ffffff; --fg:#1b1f24; --mut:#5b636c;
  --line:#d7dbe0; --card:#f6f7f9; --pass:#1f7a3d; --fail:#b3261e;
  --blocked:#8a6d00; --other:#4b5563; --rect:#c9d3de; --tap:#8fb3d9;
  --edit:#c8a2c8; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#14171a; --fg:#e6e9ec; --mut:#9aa4ae; --line:#2c3238;
    --card:#1c2126; --rect:#39424c; --tap:#3d5a7a; --edit:#5c3f5c; }
}
* { box-sizing: border-box; }
body { margin:0; padding:24px; background:var(--bg); color:var(--fg);
  font:14px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
h1 { font-size:20px; margin:0 0 4px; }
h2 { font-size:16px; margin:28px 0 8px; }
h3 { font-size:15px; margin:0 0 6px; }
.mut { color:var(--mut); }
.meta { color:var(--mut); margin:2px 0; }
dl.head { display:grid; grid-template-columns:max-content 1fr; gap:2px 14px;
  margin:12px 0 0; }
dl.head dt { color:var(--mut); }
dl.head dd { margin:0; }
.tiles { display:flex; flex-wrap:wrap; gap:10px; margin:14px 0 0; }
.tile { border:1px solid var(--line); border-radius:10px; padding:10px 14px;
  min-width:96px; background:var(--card); }
.tile .n { display:block; font-size:22px; font-weight:600; }
.tile .k { display:block; color:var(--mut); font-size:12px; }
.case { border:1px solid var(--line); border-radius:12px; padding:14px;
  margin:14px 0; background:var(--card); }
.badge { display:inline-block; border-radius:999px; padding:1px 10px;
  font-size:12px; border:1px solid var(--line); margin:0 0 8px; }
.v-pass .badge { color:var(--pass); }
.v-fail .badge { color:var(--fail); }
.v-blocked .badge { color:var(--blocked); }
.reason { margin:6px 0; }
table.steps { border-collapse:collapse; width:100%; margin:10px 0; }
table.steps th, table.steps td { border-bottom:1px solid var(--line);
  padding:5px 8px; text-align:left; vertical-align:top; font-size:13px; }
table.steps th { color:var(--mut); font-weight:500; }
.screens { display:flex; flex-wrap:wrap; gap:18px; margin:10px 0 0; }
.cap { color:var(--mut); font-size:12px; margin:0 0 4px; }
.frame { position:relative; width:360px; height:800px; border:1px solid var(--line);
  border-radius:18px; overflow:hidden; background:var(--bg); }
.frame.missing { display:flex; align-items:center; justify-content:center; }
.rect { position:absolute; border:1px solid var(--rect); border-radius:3px;
  font-size:9px; line-height:1.1; overflow:hidden; padding:1px 2px;
  color:var(--fg); }
.rect.tap { border-color:var(--tap); }
.rect.edit { border-color:var(--edit); }
.note { color:var(--mut); }
footer { margin:28px 0 0; padding-top:12px; border-top:1px solid var(--line);
  color:var(--mut); font-size:12px; }
"""


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


def _frame_html(screen_id: object, screens: object) -> str:
    """One phone frame, or an honest empty one when the screen was not stored."""
    ident = _text(screen_id, 40)
    library = screens if isinstance(screens, dict) else {}
    if not ident:
        return (
            '<div class="frame missing"><p class="note">'
            + esc(NOT_CAPTURED)
            + "</p></div>"
        )
    screen = library.get(ident)
    if not isinstance(screen, dict):
        return (
            '<div class="frame missing" data-screen="'
            + esc(ident, 40)
            + '"><p class="note">'
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
        + '">'
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


def _action_line(action: object) -> str:
    """One human line for a trace action.

    Deliberately NOT a JSON dump, and deliberately WITHOUT the action's ``text``:
    a typed literal is the one field that could carry a credential, and a
    rendering that cannot reach it beats one that masks it afterwards. It is
    also why the token ``secret`` never appears in the page.

    **This absence is the load-bearing secret control in this module**, measured
    rather than assumed: with it in place, deleting the ``run_store.redact``
    call in :func:`_trace_rows` changes no byte of the page. So the mutation
    proof for "a credential cannot reach the report" belongs HERE -- add
    ``body.get("text")`` to *extra* below and
    ``test_the_page_never_carries_the_secret_marker_or_a_typed_literal`` goes
    red.
    """
    body = action if isinstance(action, dict) else {}
    if not body:
        return _text(action, 80)
    target = body.get("target")
    target = target if isinstance(target, dict) else {}
    hint = _text(
        target.get("id")
        or target.get("rid")
        or target.get("text")
        or target.get("desc"),
        60,
    )
    extra = _text(body.get("kind") or body.get("field") or body.get("dir"), 40)
    parts = [_text(body.get("op"), 24) or "?"]
    if hint:
        parts.append("-> " + hint)
    if extra:
        parts.append("(" + extra + ")")
    return " ".join(parts)


def _trace_rows(trace: object) -> list:
    """The step table's rows, each re-redacted before anything is rendered."""
    rows = []
    for entry in list(trace or [])[:MAX_ROWS]:
        if not isinstance(entry, dict):
            continue
        # Belt and braces, and HONESTLY not canary-provable: measured by
        # mutation, deleting this line changes no byte of the page, because
        # the load-bearing control is structural -- _action_line never
        # renders an action's `text` at all, so a typed literal has no route
        # here to be masked ON. It is kept for the day a new field IS
        # rendered; the proof lives on the structural control, not here.
        safe = run_store.redact(entry)
        safe = safe if isinstance(safe, dict) else {}
        rows.append(
            {
                "index": _text(safe.get("index"), 8),
                "action": _action_line(safe.get("action")),
                "outcome": _text(safe.get("outcome"), 40) or "-",
                "ms": _text(safe.get("ms"), 12),
                "detail": _text(safe.get("detail"), MAX_TEXT),
                "before": _text(safe.get("before_screen_id"), 40),
                "after": _text(safe.get("after_screen_id"), 40),
            }
        )
    return rows


def _case_card(case: object, screens: object) -> str:
    """One case card: verdict, reason, every step, and two wireframes."""
    raw = case if isinstance(case, dict) else {}
    safe = run_store.redact(raw)
    safe = safe if isinstance(safe, dict) else {}
    tc_id = _text(safe.get("tc_id"), 40)
    verdict = _verdict_of(safe)
    rows = _trace_rows(safe.get("trace"))
    first_before = rows[0]["before"] if rows else _text(safe.get("screen_id"), 40)
    last_after = ""
    for row in reversed(rows):
        if row["after"]:
            last_after = row["after"]
            break
    steps = "".join(
        "<tr><td>"
        + esc(row["index"], 8)
        + "</td><td>"
        + esc(row["action"], 160)
        + '</td><td class="outcome">'
        + esc(row["outcome"], 40)
        + "</td><td>"
        + esc(row["ms"], 12)
        + "</td><td>"
        + esc(row["detail"])
        + "</td></tr>"
        for row in rows
    )
    table = (
        '<table class="steps"><thead><tr><th>#</th><th>action</th>'
        "<th>outcome</th><th>ms</th><th>detail</th></tr></thead><tbody>"
        + steps
        + "</tbody></table>"
        if rows
        else '<p class="note">No step has been replayed for this case yet.</p>'
    )
    reason = _text(safe.get("reason"), 400)
    return (
        '<section class="case v-'
        + esc(verdict, 24)
        + '" data-tc="'
        + esc(tc_id, 40)
        + '" data-verdict="'
        + esc(verdict, 24)
        + '" data-steps="'
        + str(len(rows))
        + '"><h3>'
        + esc(tc_id, 40)
        + " "
        + esc(safe.get("title"), 120)
        + '</h3><p class="badge">'
        + esc(verdict, 24)
        + "</p>"
        + (('<p class="reason">' + esc(reason, 400) + "</p>") if reason else "")
        + '<p class="meta">escape-hatch turns: '
        + esc(safe.get("escapes") or 0, 8)
        + "</p>"
        + table
        + '<div class="screens"><div><p class="cap">first screen</p>'
        + _frame_html(first_before, screens)
        + '</div><div><p class="cap">last screen</p>'
        + _frame_html(last_after, screens)
        + "</div></div></section>"
    )


def _stamp(value: object) -> str:
    try:
        moment = float(value or 0)
    except (TypeError, ValueError):
        return "unknown"
    if moment <= 0:
        return "unknown"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(moment))


def _header_html(run_id: str, manifest: dict, lease: dict, partial: bool) -> str:
    holder = _text(lease.get("session_id"), 40)
    displaced = _text(lease.get("taken_over_from"), 40)
    rows = [
        ("run", esc(run_id, 64)),
        ("lane", esc(manifest.get("lane") or "suite", 24)),
        ("app", esc(manifest.get("package") or "(none)", 80)),
        ("device", esc(manifest.get("serial") or "(not attached)", 40)),
        ("avd", esc(manifest.get("avd") or "(unknown)", 40)),
        ("started from", esc(manifest.get("source") or "(unrecorded)", 40)),
        ("created", esc(_stamp(manifest.get("created")), 40)),
        ("planned cases", esc(manifest.get("total") or 0, 12)),
        ("state", "in progress" if partial else "finished"),
    ]
    if holder:
        rows.append(
            (
                "lease",
                "held by session "
                + esc(holder, 40)
                + " (heartbeat "
                + esc(_stamp(lease.get("heartbeat")), 40)
                + ")",
            )
        )
    if displaced:
        rows.append(("taken over from", esc(displaced, 40)))
    body = "".join(
        "<dt>" + name + "</dt><dd>" + value + "</dd>" for name, value in rows
    )
    return (
        "<h1>Mobile run "
        + esc(run_id, 64)
        + '</h1><p class="mut">'
        + (
            "Partial report: this run is still in progress, so some cases have no "
            "verdict yet."
            if partial
            else "Complete report: every planned case reached a verdict."
        )
        + '</p><dl class="head">'
        + body
        + "</dl>"
    )


def _summary_html(tally: dict, total: int) -> str:
    """The tiles, plus the machine-readable totals the selfcheck compares."""
    ordered = [(name, int(tally.get(name) or 0)) for name in TILES]
    ordered += [
        (name, int(count)) for name, count in sorted(tally.items()) if name not in TILES
    ]
    attrs = "".join(
        " data-" + name.replace("_", "-") + '="' + str(int(count)) + '"'
        for name, count in ordered
    )
    tiles = "".join(
        '<div class="tile t-'
        + esc(name, 24)
        + '"><span class="n">'
        + str(int(count))
        + '</span><span class="k">'
        + esc(name, 24)
        + "</span></div>"
        for name, count in ordered
    )
    return (
        '<div id="'
        + SUMMARY_ID
        + '" data-total="'
        + str(int(total))
        + '"'
        + attrs
        + '><div class="tiles">'
        + tiles
        + "</div></div>"
    )


def _footer_html() -> str:
    return (
        "<footer><p>Every screen above is a WIREFRAME drawn from the element "
        "bounds the server already held; no screenshot is ever taken of the "
        "emulator, by design. A frame that says the screen was not captured is "
        "a step whose screen the run did not store -- not a step that did not "
        "happen.</p><p>Credential values are masked: the run store writes "
        "<code>***</code> for a marked value and for a credential-named key, "
        "and this page masks again and never prints an action's typed text. It "
        "cannot mask a value written under an unrecognised key with no marker; "
        "that limit is the run store's, and it is stated rather than papered "
        "over.</p><p>This is one self-contained file. It references nothing "
        "external, so it opens offline and forever.</p></footer>"
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
    cards = "".join(_case_card(case, screens) for case in cases)
    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>"
        + esc("Mobile run " + str(run_id), 120)
        + "</title><style>"
        + _CSS
        + "</style></head><body>"
        + _header_html(run_id, manifest, lease, partial)
        + _summary_html(tally, len(cases))
        + "<h2>Cases</h2>"
        + (cards or '<p class="note">No case has been checkpointed yet.</p>')
        + _footer_html()
        + '<div id="'
        + END_ID
        + '" data-cards="'
        + str(len(cases))
        + '"></div></body></html>\n'
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
