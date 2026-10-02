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
import json
import logging
import os
import re
import time
from collections import Counter
from contextvars import ContextVar
from pathlib import Path

from tools.mobile import actions as actions_mod
from tools.mobile import charter as charter_mod
from tools.mobile import (
    executor,
    explore_runner,
    media,
    paths,
    platform_info,
    report_images,
    run_store,
    screen_audit,
    screen_phone,
)
from tools.mobile import render as mobile_render
from tools.mobile_capture import flows as api_flows
from tools.mobile_evidence import crash_detector
from tools.mobile_evidence import exchanges as ev_exchanges
from tools.mobile_evidence import profiles as ev_profiles
from tools.mobile_evidence import render as ev_render

logger = logging.getLogger(__name__)

#: The report is a self-contained FOLDER, not a file: ``index.html`` beside the
#: ``media/`` its own markup references RELATIVELY, so the folder can be zipped
#: and opened anywhere. ``REPORT_FILE`` is gone rather than kept as an alias --
#: one name for one artifact, and an alias is how two answers start.
REPORT_DIR = media.REPORT_DIRNAME
INDEX_FILE = "index.html"
MEDIA_DIR = media.MEDIA_DIRNAME


def report_path(run_id: str) -> Path:
    """``runs/<run_id>/report/index.html``. Pure; creates nothing."""
    return paths.run_dir(str(run_id)) / REPORT_DIR / INDEX_FILE


#: The design file. Read once, at import: a shell that cannot be read is a
#: broken install, and it should fail loudly here rather than on the first run.
SHELL_PATH = Path(__file__).with_name("report_shell.html")

#: The CEILING the rendered wireframe box may not exceed, in CSS px. REVERSED
#: from a fixed 360x800 box (docs/DECISIONS.md -> "The frame takes the
#: device's own aspect"): a fixed frame let a landscape phone draw at a fifth
#: of the available area inside a tall empty box. `_frame_box` now fits the
#: device's own aspect INSIDE this ceiling, so a full-screen element always
#: fills its frame.
MAX_FRAME_W = 360
MAX_FRAME_H = 800

#: ``perception.MAX_ELEMENTS`` / ``MAX_ATTR_CHARS``, enforced INDEPENDENTLY here
#: because a store file is an outside input by the time this module reads it.
MAX_RECTS = 150
MAX_TEXT = 200
MAX_ROWS = 400

#: The page-size pin, shared with ``report_selfcheck`` so the two cannot drift.
#:
#: The index is ONE file inside a self-contained folder: the pictures live in
#: ``../shots/`` and the recordings in ``report/media/``, so nothing on this page
#: is an inlined payload any more. What this bounds is the MARKUP -- a run with
#: thousands of steps grows its case tables without any limit of their own, and a
#: document a browser cannot open is a report the tester cannot read at all.
#: That, and not the pictures, is the surviving constraint.
MAX_PAGE_BYTES = 8 * 1024 * 1024

#: This document's resolved frames, ``{"frames": {key: filename}}``.
#:
#: A ContextVar rather than a parameter for the reason ``mcp_handlers``'
#: ``_MOBILE_IMAGE_SPECS`` is one: the value is decided ONCE at the top of the
#: build and read in exactly one leaf (:func:`_shot_html`) that four call sites
#: reach through three intermediate builders, so threading it would rewrite five
#: signatures for a value none of them use. Two reports rendered concurrently
#: cannot see each other's frames.
_SHOTS: ContextVar = ContextVar("_SHOTS", default=None)

SUMMARY_ID = "qa-report-summary"
END_ID = "qa-report-end"

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
    "wait_until_text": "log",
    "wait_until_gone": "log",
    "wait_until_changed": "log",
    "wait_until_idle": "log",
    "done": "done",
    "escape": "note",
    "ask": "note",
    "needs": "note",
}

NOT_CAPTURED = "this screen was not captured"

_SAFE_TOKEN = re.compile(r"[^a-z0-9_]+")
_SLOT = re.compile(r"\{\{([A-Z_]+)\}\}")
_SLUG = re.compile(r"[^A-Za-z0-9_-]+")


def _compact_css(css: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    lines = (line.strip() for line in css.splitlines())
    return "\n" + "\n".join(line for line in lines if line) + "\n"


def _compact_js(js: str) -> str:
    # Block comments go too, as in the CSS. The shell's script holds no string
    # or regex with "/*" in it; test_the_page_script_keeps_no_comment binds that.
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    lines = (line.strip() for line in js.splitlines())
    return (
        "\n"
        + "\n".join(line for line in lines if line and not line.startswith("//"))
        + "\n"
    )


def _read_shell() -> str:
    """The shell as shipped: the file minus its commentary and indentation.

    The file is written to be read, and every page used to carry that reading
    -- about 30 KB of design notes per report. Comments are dropped here, ONCE,
    so :data:`SHELL`, :data:`SHELL_STYLE` and :data:`SHELL_SCRIPT` all come
    from the same compacted text and the self-check's identity pins still
    compare like with like. Only whole-line ``//`` comments leave the script
    (a ``//`` inside a line may be a string), and newlines stay so automatic
    semicolon insertion reads the code exactly as written. The one HTML
    comment kept is the source stamp, which carries a slot.
    """
    text = SHELL_PATH.read_text(encoding="utf-8")
    text = re.sub(
        r"<!--(?!\s*Rendered from: \{\{SOURCE_STAMP\}\}).*?-->\n?", "", text, flags=re.S
    )
    head, rest = text.split("<style>", 1)
    css, rest = rest.split("</style>", 1)
    body, rest = rest.split("\n<script>", 1)
    js, tail = rest.split("</script>", 1)
    return (
        head
        + "<style>"
        + _compact_css(css)
        + "</style>"
        + body
        + "\n<script>"
        + _compact_js(js)
        + "</script>"
        + tail
    )


def _between(text: str, open_tag: str, close_tag: str) -> str:
    start = text.index(open_tag) + len(open_tag)
    return text[start : text.index(close_tag, start)]


#: The design, verbatim. Neither constant is ever interpolated into.
SHELL = _read_shell()
SHELL_STYLE = _between(SHELL, "<style>", "</style>")
SHELL_SCRIPT = _between(SHELL, "\n<script>", "</script>")

_CSS_CLASS = re.compile(r"\.(-?[A-Za-z_][\w-]*)")
_CSS_NOT = re.compile(r":not\([^()]*\)")
_CLASS_ATTR = re.compile(r'class="([^"]*)"')
#: Every class the script can PUT on the page: an element's ``className``, a
#: ``classList.add``/``toggle``, and a ``class="..."`` in markup it writes. A class it
#: only queries (``$$('li.seq')``, ``contains('is-' + k)``) needs a rule only when the
#: markup carries it, and then ``live`` has it -- counting every word the script held
#: kept ~3 KB of sequence and exchange rules on pages that draw neither.
_SCRIPT_CLASSES = frozenset(
    token
    for value in re.findall(
        r"className\s*=\s*'([^']*)'"
        r"|classList\.(?:add|toggle)\('([^']*)'"
        r'|class="([^"]*)"',
        SHELL_SCRIPT,
    )
    for part in value
    for token in part.split()
)


def _block_end(css: str, start: int) -> int:
    """Index just past the ``}`` closing the block whose ``{`` is at ``start``."""
    depth = 0
    for i in range(start, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if not depth:
                return i + 1
    return len(css)


def _css_rules(css: str, at: str = "") -> list:
    """``css`` as ``(at_rule, rule)`` pairs in source order; ``@media`` opened."""
    out: list = []
    i = 0
    while True:
        j = css.find("{", i)
        if j < 0:
            return out
        prelude = css[i:j].strip()
        end = _block_end(css, j)
        if prelude.startswith("@media"):
            out += _css_rules(css[j + 1 : end - 1], prelude)
        else:
            out.append((at, prelude + css[j:end].strip()))
        i = end


def _page_style(markup: str) -> str:
    """The shell's rules, in the shell's order, that can match ``markup``.

    The stylesheet styles every kind of run -- the scripted cards, their
    filters and wireframes -- and a page carried all of it: about 36 KB of an
    exploratory page styled nothing on it. A rule stays when one of its
    selectors names only classes the markup carries or the script creates; a selector
    with no class, and every other at-rule, always stays. Nothing is added or
    rewritten, so the page's style is still the shell's text.
    """
    live = {tok for value in _CLASS_ATTR.findall(markup) for tok in value.split()}

    def usable(rule: str) -> bool:
        if rule.startswith("@"):
            return True
        selector = _CSS_NOT.sub("", rule.split("{", 1)[0])
        return any(
            all(
                name in live or name in _SCRIPT_CLASSES
                for name in _CSS_CLASS.findall(branch)
            )
            for branch in selector.split(",")
        )

    out: list = []
    group, rules = None, []
    for at, rule in _css_rules(SHELL_STYLE) + [(None, "")]:
        if at != group:
            if rules:
                out.append(
                    (group + "{" + "\n".join(rules) + "}")
                    if group
                    else "\n".join(rules)
                )
            group, rules = at, []
        if at is not None and usable(rule):
            rules.append(rule)
    return "\n" + "\n".join(out) + "\n"


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
    return html.escape(
        _neutralize_markers(_text(_mask_prose(value), limit)), quote=True
    )


#: A credential NAMED in prose, and the value written after it: "password Qwerty123",
#: "OTP 4821", "national ID" and its ten digits. A goal, a case title and a turn's action are
#: written by a person, and people put the login in the sentence. The value must carry
#: a digit -- "the password field" and "Tapped the password" name no value. A
#: digitless value is caught by `_PROSE_CREDENTIAL_SET` below when the sentence marks
#: it as a value; a bare one ("password Hunter") only by the run-scoped list, when the
#: run typed it into a field marked secret or named as a credential.
#:
#: Deliberately NOT a key=value sweep: ``tenantToken=UNMARKED-CANARY-42`` still
#: renders, as `test_the_secret_refusal_cannot_catch_an_unmarked_value` pins. The terms
#: are whole words, so ``tenantToken`` is not ``token`` (which is not a term here).
_CREDENTIAL_TERMS = r"\b(national[ -]?id(?: number)?|id number|iqama|password|passwd|passcode|pin(?: code)?|otp)\b"
_PROSE_CREDENTIAL = re.compile(
    _CREDENTIAL_TERMS + r"(\s*(?:is|was|=|:)?\s*)"
    r"([^\s,;\"'<>]*\d[^\s,;\"'<>]*?)(?=[.)]?(?:[\s,;\"'<>]|$))",
    re.IGNORECASE,
)
#: The same terms with the value MARKED as one, so it needs no digit: after ``:`` or
#: ``=`` ("password: Hunter"), or quoted ("password is 'Hunter Horse'"). Group 3 keeps
#: the quotes, so the mask replaces them too.
_PROSE_CREDENTIAL_SET = re.compile(
    _CREDENTIAL_TERMS + r"(\s*[:=]\s*|\s+(?:(?:is|was)\s+)?(?=[\"']))"
    r"(\"[^\"<>\n]+\"|'[^'<>\n]+'|[^\s,;\"'<>]+?)(?=[.)]?(?:[\s,;\"'<>]|$))",
    re.IGNORECASE,
)
#: A remembered value shorter than this is masked only where the phrase above finds
#: it: "4821" blanked everywhere would eat timestamps, counts and file names.
_SECRET_MIN = 6
#: This page's remembered credential values, longest first, bound by :func:`_document`.
_SECRETS: ContextVar = ContextVar("_SECRETS", default=())


def _mask_prose(value: object) -> object:
    """*value* with every credential value this page knows of replaced by the mask.

    Runs before `_text` caps the length, so a cap can never cut a secret in half
    and leave the half the mask would have matched. Non-strings pass through.
    """
    if not isinstance(value, str) or not value:
        return value
    # Case-blind: the card's search index lower-cases its text before it gets here,
    # and "Hunter-Horse" typed is "hunter-horse" there.
    for secret in _SECRETS.get():
        if secret.casefold() in value.casefold():
            value = re.sub(
                r"(?<!\w)" + re.escape(secret) + r"(?!\w)",
                actions_mod.SECRET_MASK,
                value,
                flags=re.IGNORECASE,
            )
    for phrase in (_PROSE_CREDENTIAL_SET, _PROSE_CREDENTIAL):
        value = phrase.sub(
            lambda m: m.group(1) + m.group(2) + actions_mod.SECRET_MASK, value
        )
    return value


def _run_secrets(*records: object) -> tuple:
    """Every credential value the run's own records name or type, longest first.

    Two sources: a value written after a credential term in any string (the goal
    names the national ID the tester then types into an unmarked field), and the
    text of a ``type`` action the run marked secret or aimed at a credential field.
    Only values of `_SECRET_MIN` characters or more are kept.
    """
    found: set = set()
    stack = list(records)
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if str(item.get("op") or "") == "type" and (
                item.get("secret") or actions_mod.is_credential_action(item)
            ):
                found.add(_text(item.get("text"), 200))
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
        elif isinstance(item, str):
            for phrase in (_PROSE_CREDENTIAL, _PROSE_CREDENTIAL_SET):
                found.update(m.group(3).strip("\"'") for m in phrase.finditer(item))
    kept = {value for value in found if len(value) >= _SECRET_MIN}
    return tuple(sorted(kept, key=lambda value: (-len(value), value)))


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
#: The literal `session._explore_case` writes as an exploratory turn's one step.
EXPLORE_GOAL_PREFIX = "Explore towards the goal: "
EXPLORATORY = "Exploratory"


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


def _case_type(case: object, manifest: dict) -> str:
    body = case if isinstance(case, dict) else {}
    planned = _planned_case(manifest, _text(body.get("tc_id"), 40))
    return _text(planned.get("type") or body.get("type"), 40)


def _run_kind(cases: object, manifest: dict | None = None) -> str:
    """``exploratory``, ``scripted`` or ``mixed``; an empty run is ``scripted``."""
    manifest = manifest if isinstance(manifest, dict) else {}
    kinds = {_case_type(case, manifest) == EXPLORATORY for case in list(cases or [])}
    if kinds == {True}:
        return "exploratory"
    if True in kinds:
        return "mixed"
    return "scripted"


def _run_goals(manifest: dict) -> list:
    explore = manifest.get("explore") if isinstance(manifest, dict) else None
    explore = explore if isinstance(explore, dict) else {}
    charter = explore.get("charter")
    charter = charter if isinstance(charter, dict) else {}
    goal = _text(charter.get("goal") or explore.get("goal"), 2000)
    return [goal] if goal else []


def _goal_of(case: object, manifest: dict) -> str:
    """The goal text an exploratory turn was driving at, read from its one step."""
    body = case if isinstance(case, dict) else {}
    planned = _planned_case(manifest, _text(body.get("tc_id"), 40))
    for step in planned.get("steps") or []:
        action = _text(step.get("action") if isinstance(step, dict) else "", 2000)
        if action.startswith(EXPLORE_GOAL_PREFIX):
            return action[len(EXPLORE_GOAL_PREFIX) :].strip()
    return ""


def _derive_goals(cases: object, manifest: dict) -> list:
    """Exploratory turns grouped by goal, in first-seen order.

    ``[{"goal": text, "cases": [case, ...]}]``. The run's charter goal is
    listed first even before a turn names it; a turn whose own step names a
    different goal opens a group of its own; a turn naming no goal at all is
    its own group labelled by its title, never dropped. Scripted cases are not
    goals and are skipped.
    """
    manifest = manifest if isinstance(manifest, dict) else {}
    groups: list = []
    by_goal: dict = {}
    for goal in _run_goals(manifest):
        by_goal[goal] = {"goal": goal, "cases": []}
        groups.append(by_goal[goal])
    for case in list(cases or []):
        if _case_type(case, manifest) != EXPLORATORY:
            continue
        goal = _goal_of(case, manifest)
        if not goal:
            body = case if isinstance(case, dict) else {}
            groups.append(
                {
                    "goal": _text(body.get("title") or body.get("tc_id"), 250),
                    "cases": [case],
                }
            )
            continue
        # A charter goal is stored whole while a turn's copy may be the
        # truncated title-length form: match on the shared prefix.
        match = next(
            (
                g
                for text, g in by_goal.items()
                if text == goal or text.startswith(goal) or goal.startswith(text)
            ),
            None,
        )
        if match is None:
            match = by_goal[goal] = {"goal": goal, "cases": []}
            groups.append(match)
        match["cases"].append(case)
    return [g for g in groups if g["cases"]]


def _goal_result(cases: list) -> str:
    """A goal's one result: the newest turn that reached a verdict decides it.

    Exploratory turns before the committing one carry no verdict of their own
    (they are steps towards the goal), so they cannot fail it; a goal whose
    turns never reached one is ``unfinished``.
    """
    for case in reversed(list(cases or [])):
        body = case if isinstance(case, dict) else {}
        verdict = _token(body.get("verdict"))
        if verdict in DONE_VERDICTS:
            return _unify_verdict(verdict)
    return "unfinished"


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
        except (TypeError, ValueError, OverflowError):
            return None
    left, right = sorted((numbers[0], numbers[2]))
    top, bottom = sorted((numbers[1], numbers[3]))
    return (left, top, right, bottom)


# The largest device extent this module accepts out of a store file. The WRITER
# cannot produce more -- `perception.parse_bounds` matches at most seven digits
# -- so a larger value in a store file did not come from a dump this server
# pruned. `scale_bounds` keeps its no-raise guarantee either way; this is the
# repo's "every cap is bounded from above" convention applied to a value whose
# own docstring calls it untrusted input.
MAX_DEVICE_EXTENT = 9999999


def _device_size(screen: object) -> tuple:
    """The device viewport: the stored display rectangle, or the elements' extent.

    Not a hardcoded phone size: a tablet AVD is legitimate. ``(0, 0)`` when the
    dump carries no usable geometry, which is what stops the divide.

    **The stored root is preferred because ``max()`` is not the viewport.** A
    ``RecyclerView``/``ScrollView`` reports its CONTENT height, so on a scrollable
    screen the lowest element bottom routinely exceeds the display -- and
    :func:`scale_bounds`, which scales by ``min(MAX_FRAME_W/dev_w, MAX_FRAME_H/dev_h)``,
    then shrinks EVERY element to fit a device taller than the real one. The
    visible result is every scrollable screen squeezed into the top of its frame.

    ``perception.prune`` stores that rectangle as ``root_bounds``: the device's
    own display, from ``adb.display_size``, oriented by the dump's ``rotation``
    -- NOT a window chosen out of the dump. Four rounds of choosing one are in
    docs/DECISIONS.md -> *The display size is not in the dump*. RIGHT and BOTTOM
    are the size: the origin is always ``(0, 0)``, so the extent measured from
    it is what :func:`scale_bounds` maps against.

    Where the device could not answer AND no depth-1 window was measurable, the
    panel is genuinely unknown and ``root_bounds`` is ``[]`` -- which since the
    panel stopped borrowing the frame's rule is reachable with a FULL element
    list, not just on an empty dump. So ``frame_bounds`` is read next: the
    rectangle ``prune`` actually filtered with, which is at least as large as
    everything laid out. It is not the display and does not claim to be; it is a
    scale the PRODUCER derived rather than one this function improvises.

    The ``max()`` walk survives below as the last resort for one caller that
    still happens: a screen stored by a build that wrote neither key. It must
    not become the ordinary path for an unknown panel -- it walks the pruned
    ELEMENTS, so on a scrollable screen it returns the container's content
    height, and that is the squeeze described above.

    This is a store file -- an outside input by the time it is read -- so a wrong
    type, a wrong length, a bool, a non-number, a negative origin or a zero-area
    rect must all DEGRADE to the fallback rather than raise or hand back a zero
    divisor. An INVERTED rect does not degrade: :func:`_bounds_of` sorts
    left/right and top/bottom deliberately, so ``[1080, 2400, 0, 0]`` normalises
    to ``(0, 0, 1080, 2400)`` and is accepted. That is the helper's existing,
    documented behaviour and it is pinned by test rather than changed here.
    """
    body = screen if isinstance(screen, dict) else {}
    # In priority order, and derived ONCE: the display if the producer knew it,
    # then the frame it filtered with, then the walk. Two sources, one set of
    # clauses -- checking them twice is how a second copy drifts from the first.
    for key in ("root_bounds", "frame_bounds"):
        rect = _bounds_of({"bounds": body.get(key)})
        if (
            rect is not None
            and rect[0] >= 0
            and rect[1] >= 0
            and 0 < rect[2] <= MAX_DEVICE_EXTENT
            and 0 < rect[3] <= MAX_DEVICE_EXTENT
        ):
            # Each clause is reachable on its own, which is what makes it a
            # guard rather than decoration. `_bounds_of` SORTS, so
            # `rect[2] >= rect[0]` always holds -- but with the origin only
            # required to be non-negative, `[0,0,0,2400]` still reaches and
            # fails `rect[2] > 0`, and `[0,0,1080,0]` still reaches and fails
            # `rect[3] > 0`. A negative origin fails the first two and never
            # reaches either.
            return rect[2], rect[3]
    width = 0
    height = 0
    for element in body.get("elements") or []:
        box = _bounds_of(element)
        if box is None:
            continue
        width = max(width, box[2])
        height = max(height, box[3])
    return width, height


def _frame_scale(dev_w: int, dev_h: int) -> float:
    """THE scale factor for one device: device pixels -> frame pixels.

    One producer, because two consumers need the SAME float and not merely the
    same formula. :func:`_frame_box` multiplies it to size the box;
    :func:`scale_bounds` multiplies it to place every rect inside that box. When
    `scale_bounds` re-derived its own scale from the already-rounded box, the
    box's integer aspect differed from the device's by a fraction of a pixel and
    a full-screen element came out one pixel short of the frame it fills -- the
    same input, derived twice, drifting. Callers must guarantee both arguments
    are positive; `_frame_box`'s guard is what establishes that.
    """
    return min(MAX_FRAME_W / float(dev_w), MAX_FRAME_H / float(dev_h))


def _frame_box(dev_w: int, dev_h: int) -> tuple:
    """The rendered wireframe box for one device: its own aspect, contained
    inside the ``MAX_FRAME_W``x``MAX_FRAME_H`` ceiling.

    ONE producer of frame size -- :func:`scale_bounds` and :func:`wireframe`
    both call this rather than each deriving it, so the clamp and the drawn
    box can never disagree (the two-derivations trap this project has hit
    before). Guarantees: both returned values are ``int``, ``>= 1``, and
    ``<= `` their respective ceiling. A non-positive device (the guard this
    work owns -- see the mutation clause ratchet) falls back to the full
    ceiling box rather than dividing by zero or returning a degenerate one.
    """
    if dev_w <= 0 or dev_h <= 0:
        return (MAX_FRAME_W, MAX_FRAME_H)
    scale = _frame_scale(dev_w, dev_h)
    # TRUNCATE, exactly as `scale_bounds` does for w/h. The two must round the
    # same way or a full-device element lands a pixel short of its own frame:
    # round() here gave a 2208px-tall device a 450px box whose full-screen rect
    # scaled to 449. Same input, two derivations, one drifting -- the trap this
    # producer exists to close, reintroduced by the arithmetic rather than by a
    # second call site. Truncation is also the safe direction: the box can only
    # be smaller than the exact fit, never larger, so nothing paints outside it.
    frame_w = min(MAX_FRAME_W, max(1, int(dev_w * scale)))
    frame_h = min(MAX_FRAME_H, max(1, int(dev_h * scale)))
    return (frame_w, frame_h)


def scale_bounds(box: object, dev_w: int, dev_h: int) -> tuple | None:
    """Device pixels -> a rect inside this device's own frame, or None.

    Guarantees, unconditionally, for every tuple it returns: all four values are
    ``int``, ``x >= 0``, ``y >= 0``, ``x + w <= frame_w`` and ``y + h <=
    frame_h``, where ``frame_w, frame_h = _frame_box(dev_w, dev_h)`` and so
    ``frame_w <= MAX_FRAME_W`` and ``frame_h <= MAX_FRAME_H`` always. A
    malformed dump must not be able to paint outside the frame or raise.
    """
    try:
        left, top, right, bottom = (int(value) for value in box)
        left = min(max(0, left), int(dev_w))
        right = min(max(0, right), int(dev_w))
        top = min(max(0, top), int(dev_h))
        bottom = min(max(0, bottom), int(dev_h))
        # THE guard. A zero or negative device collapses every clamped box to
        # right <= left (or bottom <= top), so this one check is what refuses
        # it -- and it is the only reachable one. Two explicit zero-device
        # checks (here and in wireframe) sat in front of it and both graded
        # dead under mutation (round-14): removing either changed nothing.
        # The division below is reached only when right > left, which needs
        # dev_w > 0.
        if right <= left or bottom <= top:
            return None
        frame_w, frame_h = _frame_box(dev_w, dev_h)
        # The SAME float `_frame_box` sized the box with -- never a scale
        # re-derived from the rounded box, which drifts by a pixel on the axis
        # the ceiling did not constrain.
        scale = _frame_scale(dev_w, dev_h)
        x = int(left * scale)
        y = int(top * scale)
        w = max(1, int((right - left) * scale))
        h = max(1, int((bottom - top) * scale))
        x = min(x, frame_w - 1)
        y = min(y, frame_h - 1)
        w = min(w, frame_w - x)
        h = min(h, frame_h - y)
        return (x, y, w, h)
    except (TypeError, ValueError, OverflowError):
        return None


def wireframe(screen: object) -> dict:
    """``{"rects", "device", "scaled", "clipped", "outside"}`` for one pruned
    screen. ``clipped`` counts elements drawn as the sliver that fits the
    display; ``outside`` counts elements with area but none of it on the
    display, which are not drawn; the page discloses both.

    ``scaled`` is False when the dump had no usable geometry -- the report then
    draws a labelled empty frame rather than a plausible-looking wrong one.
    """
    body = screen if isinstance(screen, dict) else {}
    dev_w, dev_h = _device_size(body)
    # No zero-device check HERE: `scale_bounds` refuses a zero device for every
    # box and the loop then produces no rects, which is the same frame the
    # removed early return built by hand. Two checks for one condition was a
    # second copy waiting to drift (round-13 review, R3/R6).
    rects = []
    clipped = 0
    outside = 0
    for element in list(body.get("elements") or [])[:MAX_RECTS]:
        box = _bounds_of(element)
        if box is None:
            continue
        # Two names for two facts, counted BEFORE the scale decides whether
        # anything is drawn (round-14 review, H1: counting after the
        # `placed is None` skip omitted every element that lay entirely off
        # the display, so a screen that lost 2 of 4 elements said "1").
        #   clipped -- has area inside the display but reaches past it; drawn
        #              as the sliver that fits.
        #   outside -- has area, none of it on the display; not drawn at all.
        # A degenerate (zero-area) box is neither: there is nothing to draw
        # and nothing was lost.
        has_area = box[2] > box[0] and box[3] > box[1]
        past_edge = box[0] < 0 or box[1] < 0 or box[2] > dev_w or box[3] > dev_h
        placed = scale_bounds(box, dev_w, dev_h)
        if placed is None:
            if has_area and dev_w > 0 and dev_h > 0:
                outside += 1
            continue
        if past_edge:
            clipped += 1
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
    # `scaled` is a claim about the PICTURE, not about the geometry that went
    # into it: this function's contract is that a False draws a labelled empty
    # frame rather than a plausible-looking wrong one. A screen whose elements
    # all lie outside the display -- reachable since the visibility frame was
    # widened past it -- scales every one of them to nothing and would otherwise
    # return an empty frame claiming to be a real one.
    frame_w, frame_h = _frame_box(dev_w, dev_h)
    return {
        "rects": rects,
        "device": [dev_w, dev_h],
        "scaled": bool(rects),
        "clipped": clipped,
        "outside": outside,
        "frame_w": frame_w,
        "frame_h": frame_h,
    }


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
    except (TypeError, ValueError, OverflowError):
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
    """A stored moment as ``YYYY-MM-DD HH:MM:SS``, or ``"unknown"``.

    THE WHOLE CALL IS INSIDE THE GUARD, and the reason is a measured page loss,
    not tidiness. The coercion was guarded and ``localtime`` was not, so a
    manifest carrying a non-finite ``created`` -- ``json.loads`` accepts a bare
    ``Infinity`` -- raised ``OverflowError`` out of ``_facts_strip``, through
    ``_document``, into ``render``'s outer ``except``: NO PAGE AT ALL, over one
    cell. A helper that promises "any moment" must be total for any moment, and
    ``"unknown"`` is what it already returns for a moment it cannot read, so no
    caller sees a new shape.

    ``OSError`` joins the tuple because that is what ``localtime`` raises for an
    out-of-range (rather than non-finite) value on some platforms, and
    ``OverflowError`` must stay in it for `tests/test_coercion_guards.py`, whose
    ``blind_guards`` rule rejects a handler whose names are a subset of
    ``{TypeError, ValueError}``.
    """
    try:
        moment = float(value or 0)
        if moment <= 0:
            return "unknown"
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(moment))
    except (TypeError, ValueError, OverflowError, OSError):
        return "unknown"


def _short_stamp(value: object) -> str:
    """The same moment, shorter, and total for the same reason.

    NOT reachable with a disk value today: both call sites pass ``time.time()``.
    It is hardened anyway because it is the same shape one function away, and
    the reachability that protects it is a property of its CALLERS -- the next
    one to pass a stored moment would reintroduce the defect this batch has now
    fixed twice. Being unreachable, this half has no mutant, which is stated
    rather than papered over with a kill it cannot earn.
    """
    try:
        moment = float(value or 0)
        if moment <= 0:
            return "unknown"
        return time.strftime("%d %b %Y %H:%M", time.localtime(moment))
    except (TypeError, ValueError, OverflowError, OSError):
        return "unknown"


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


def _sechead(
    sid: str, title: str, label: str, lede: str, body: str, level: int = 2
) -> str:
    """``lede`` is markup built through :func:`esc`; the ids are constants.

    ``level`` 3 is for a section only ever drawn inside a Diagnostics part, which
    ``<h2>Diagnostics</h2>`` already heads: an h2 there reads, in a screen reader's
    outline, as a sibling of Diagnostics rather than a part of it.
    """
    tag = "h3" if level == 3 else "h2"
    return (
        '\n  <section id="'
        + sid
        + '">\n    <div class="sechead"><'
        + tag
        + ">"
        + esc(title, 80)
        + "</"
        + tag
        + '><span class="label">'
        + esc(label, 80)
        + "</span></div>\n"
        + (('    <p class="lede">' + lede + "</p>\n") if lede else "")
        + body
        + "\n  </section>\n"
    )


# ── the wireframe phone ────────────────────────────────────────────────────────


#: Said instead of drawing the wrong look. "Not captured" would be false here:
#: the screen WAS captured, several times, and this frame does not know which
#: of them it means.
AMBIGUOUS_LOOK = (
    "This screen was seen more than once and this frame does not record which "
    "look it means, so nothing is drawn rather than the most recent one."
)


def _look_is_ambiguous(ident: str, library: dict) -> bool:
    """Does this BARE screen id stand for more than one stored look?

    False for an observation key -- that already names its look, and the dash it
    carries is what tells the two apart. Counting the observations belonging to
    this screen is what lets a screen seen exactly once keep rendering, instead
    of being suppressed by a guard too coarse to tell the cases apart.

    THE DASH FAST PATH IS GONE, and its removal is the fix. It returned False
    for any ident carrying a dash, which is right for an observation key and
    WRONG for the screen id ``_document`` concedes is admissible: `_RUN_ID_RE`
    accepts ``aabbccdd0011-000000000000``, and such an id with two stored looks
    was answered "unambiguous" by the fast path and "ambiguous" by the count --
    one question derived twice inside one function, disagreeing. The count is
    the only derivation now. It still answers False for a real observation key,
    because the prefix it scans for is that key plus another dash and no library
    entry can start with it, so nothing about the ordinary case changed.
    """
    if not isinstance(ident, str):
        return False
    prefix = ident + "-"
    seen = 0
    for key in library if isinstance(library, dict) else {}:
        if isinstance(key, str) and key.startswith(prefix):
            seen += 1
            if seen > 1:
                return True
    return False


#: The three answers a frame key can get from the merged library. ONE producer,
#: one meaning, EVERY consumer: `_frame_html` and `_phone_html` both ask
#: :func:`_resolve_look` and neither derives the question again. Three review
#: rounds were three consumers each deriving it separately, and each got it
#: wrong somewhere else.
LOOK_EXACT = "exact"
LOOK_UNCAPTURED = "uncaptured"
LOOK_AMBIGUOUS = "ambiguous"


def _resolve_look(screen_id: object, screens: object) -> tuple[str, dict | None, str]:
    """A frame key against the merged library: ``(ident, screen or None, verdict)``.

    THE resolver. A bare screen id says WHICH screen and cannot say WHAT was on
    it: the screen library holds the LAST look, and a screen observed more than
    once has several. Resolving a frame against it draws the newest content
    under whatever caption the caller wrote -- on a conversation, a reply the app
    had not yet produced, shown to a non-technical tester as evidence of what
    their own action did.

    ``LOOK_AMBIGUOUS`` means TWO OR MORE stored looks. Exactly one is not
    ambiguous -- the bare id and that single observation are the same bytes -- so
    a settings walk and every single-look screen resolve as they always did.
    That is what keeps this a guard and not a regression.

    The verdict is returned rather than acted on because the two consumers
    render it differently -- the fold draws an empty frame, the phone an empty
    handset -- and the point of this function is that neither of them decides
    the QUESTION.
    """
    ident = _text(screen_id, 40)
    library = screens if isinstance(screens, dict) else {}
    if not ident:
        return "", None, LOOK_UNCAPTURED
    screen = library.get(ident)
    if not isinstance(screen, dict):
        return ident, None, LOOK_UNCAPTURED
    if _look_is_ambiguous(ident, library):
        return ident, None, LOOK_AMBIGUOUS
    return ident, screen, LOOK_EXACT


#: The ``src`` prefix every ``img`` on this page carries, and THE definition of it.
#:
#: A FOLDER-RELATIVE PATH, not a ``data:`` payload. The index lives in ``report/``
#: inside the run directory and the PNGs live in ``../shots/``, so the whole folder
#: travels as one artifact -- which is already the documented contract (the
#: recordings under ``report/media/`` have always worked this way). A lone
#: ``index.html``, separated from its folder, shows no screens; the page says so in
#: its own standfirst rather than rendering broken images silently.
#:
#: Declared here, in the emitter, and IMPORTED by the guard
#: (``report_selfcheck.SHOT_SRC = report.SHOT_SRC``) rather than restated there.
#: The direction is forced: ``report_selfcheck`` already does
#: ``from tools.mobile import report``, so the reverse import would be a cycle.
#: One object, one meaning -- this very change inverted the MEANING of the value
#: while the name stayed, and every consumer that bound the constant rather than
#: the literal needed no edit at all. That is the property, demonstrated.
SHOT_SRC = "../" + run_store.SHOTS_DIR + "/"

#: An observation for which NOTHING was ever stored.
#:
#: A DIFFERENT name and different words from the note below, on purpose: "no
#: picture was ever taken" and "a picture exists and this page declined it" are
#: different facts about the run and only one of them is worth investigating.
#: Collapsing them is the half-wired-sentinel failure this repository has
#: already paid for -- the same three-state rule ``tools/mobile/screenshot.py``
#: states for the packet's own note.
SHOT_MISSING_NOTE = (
    "No picture of this screen was stored: the capture did not succeed on that "
    "turn, or this run is older than stored frames. The drawing below is "
    "composed from the element list, which is all this page has."
)


def _shots(run_id: object, library: object) -> dict:
    """``{"frames": {key: filename}}``. Never raises.

    ONE producer for every picture on this page. It answers exactly one question --
    "does a non-empty PNG exist on disk for this observation?" -- by reading the
    file, so the page can never claim a picture that is not there.

    *library* is the MERGED book :func:`_merged_library` builds -- screens AND
    observations -- because that is what a frame resolves against, and a frame the
    page cannot resolve is a frame no picture belongs under.

    THERE IS NO BUDGET ANY MORE. Nothing is inlined, so the page costs the same
    whether it references one picture or two hundred, and a "this report declined
    to show it" state has no producer left. It was DELETED rather than kept as an
    always-empty list: a sentence with no producer is a fabricated absence, and an
    always-empty branch is how a dead state survives a rewrite.
    """
    frames: dict = {}
    try:
        book = library if isinstance(library, dict) else {}
        for ident in sorted(str(key) for key in book):
            data = (run_store.read_shot(str(run_id), ident) or {}).get("content")
            if not isinstance(data, (bytes, bytearray)) or not data:
                continue
            frames[ident] = ident + ".png"
    except Exception:  # never-raise: a picture is not a verdict
        logger.exception("mobile.report._shots failed")
    return {"frames": frames}


def _shot_html(ident: str) -> str:
    """The stored PNG of *ident*, referenced by path, or the honest note for its absence.

    *ident* is what :func:`_resolve_look` RESOLVED, and this function is called only
    on its ``LOOK_EXACT`` answer. That is load-bearing: under ``LOOK_AMBIGUOUS`` the
    key is a bare screen id with two or more stored looks behind it, and a picture
    drawn there would be one arbitrary look captioned as the step's own -- the
    fabricated-evidence defect ``_resolve_look`` exists to stop, in a form a tester
    is even less able to question because it is a photograph.

    A PATH and never a ``data:`` URI: see :data:`SHOT_SRC`. The self-check enforces
    that every ``img`` on the page starts with that prefix and climbs exactly one
    level -- ``report_selfcheck._pin_links`` checks IDENTITY, as it already does for
    the shell's one script and for every clip source.

    The ``<img>`` is emitted ONLY from a non-empty entry in ``frames``, which only a
    non-empty file on disk can produce, so a capture that failed can never leave
    this page claiming a picture exists.
    """
    book = _SHOTS.get() or {}
    filename = str((book.get("frames") or {}).get(ident) or "")
    if filename:
        return (
            '<figure class="shot"><img alt="The screen as the device drew it" '
            'loading="lazy" src="' + SHOT_SRC + filename + '"></figure>'
        )
    return '<p class="wirenote shotnote">' + esc(SHOT_MISSING_NOTE, 300) + "</p>"


def _frame_html(screen_id: object, screens: object) -> str:
    """One frame, or an honest empty one when the screen was not stored."""
    ident, screen, verdict = _resolve_look(screen_id, screens)
    if not ident:
        return (
            '<div class="frame missing"><p class="wirenote">'
            + esc(NOT_CAPTURED)
            + "</p></div>"
        )
    if verdict == LOOK_UNCAPTURED:
        return (
            '<div class="frame missing" data-screen="'
            + esc(ident, 40)
            + '"><p class="wirenote">'
            + esc(NOT_CAPTURED)
            + "</p></div>"
        )
    if verdict == LOOK_AMBIGUOUS:
        # THE INVARIANT, DECIDED IN `_resolve_look` AND HONOURED HERE.
        #
        # A bare screen id says WHICH screen. It cannot say WHAT was on it: the
        # screen library holds the LAST look, and a screen observed more than
        # once has several. Resolving a frame against it draws the newest
        # content under whatever caption the caller wrote -- on a conversation,
        # a reply the app had not yet produced, shown to a non-technical tester
        # as evidence of what their action did.
        #
        # This is the THIRD consumer to make that mistake: the sequence row's
        # "Screen after", the overview's opening-screen count, and the case
        # card's "Last screen" -- each found in a separate review round, each
        # patched separately, and two of them introduced by the change that
        # created the second library in the first place. So the rule stops being
        # something every caller must remember and becomes something no caller
        # can get wrong.
        #
        # Ambiguous means TWO OR MORE stored looks. Exactly one is not
        # ambiguous -- the bare id and that single observation are the same
        # bytes -- so a settings walk and every single-look screen render as
        # they always did. That is what keeps this a guard and not a regression.
        return (
            '<div class="frame missing" data-screen="'
            + esc(ident, 40)
            + '"><p class="wirenote">'
            + esc(AMBIGUOUS_LOOK)
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
        '<div class="frame" style="width:'
        + str(int(frame["frame_w"]))
        + "px;height:"
        + str(int(frame["frame_h"]))
        + 'px" data-screen="'
        + esc(ident, 40)
        + '" data-rects="'
        + str(len(frame["rects"]))
        + '" data-scaled="'
        + ("1" if frame["scaled"] else "0")
        + '" data-clipped="'
        + str(int(frame.get("clipped") or 0))
        + '" data-outside="'
        + str(int(frame.get("outside") or 0))
        + '">'
        + rects
        + _geometry_note(frame)
        + "</div>"
    )


def _geometry_note(frame: dict) -> str:
    """The frame's disclosure of what the picture could not show, or ''.

    Rendered as ``.frame .wirenote.geom`` -- the shell styles it (round-14
    review, H3: the first version emitted an unstyled class in-flow under the
    absolutely positioned rects, where nobody could read it) and pins it to the
    frame's foot above the rects. Both counts are ints this module computed, so
    the interpolation is the one kind that needs no escaping.
    """
    clipped = int(frame.get("clipped") or 0)
    outside = int(frame.get("outside") or 0)
    if not clipped and not outside:
        return ""
    parts = []
    if clipped:
        parts.append(
            str(clipped) + " element(s) extend past the display and are drawn clipped"
        )
    if outside:
        parts.append(
            str(outside) + " element(s) lie entirely off the display and are not drawn"
        )
    return '<p class="wirenote geom">' + "; ".join(parts) + "</p>"


def _phone_html(
    title: str, sub: str, screen_id: object, screens: object, app: str
) -> str:
    """The screen as the app drew it, with the element map folded beneath it.

    Both pictures come from the same pruned dump. ``screen_phone.compose`` reads
    the elements' text, labels and bounds into the app bar, bubbles, cards, chips
    and composer the user saw; the wireframe keeps the exact rectangles for
    anyone who needs them. Above both sits the PNG the lane captured at that
    observation, when one was stored and this page could afford it; where it
    could not, the note in its place says which of those two things happened.
    """
    ident, screen, verdict = _resolve_look(screen_id, screens)
    if screen is not None:
        # The photograph first, the composition under it: the drawing is an
        # annotation of the screen, not a substitute for it, and a reader who
        # sees the real thing first can tell which is which.
        #
        # INSIDE this branch and nowhere else. `_resolve_look` has already said
        # this key names ONE stored look; on its `LOOK_AMBIGUOUS` answer the
        # else-branch below draws an empty handset and says the look is
        # unknowable, and a photograph hung above that sentence would contradict
        # it with the one kind of evidence a tester cannot argue with.
        drawn = _shot_html(ident) + screen_phone.compose(screen, esc=esc, app=app)
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
        # THE PICTURE GETS THE SAME ANSWER AS THE FOLD BENEATH IT. Until this
        # change the fold said the look was unknowable while the phone above it
        # drew the newest one: one page contradicting itself, because this
        # function resolved the library itself and the round-two guard governed
        # only `_frame_html`, which is the fold. `ph-empty` is an existing rule
        # in ``report_shell.html`` -- no new class name is introduced, because a
        # class with no rule renders unstyled with nothing failing.
        drawn = (
            '<div class="phone" dir="auto"><div class="ph-scroll"><div class="ph-empty">'
            + esc(AMBIGUOUS_LOOK if verdict == LOOK_AMBIGUOUS else NOT_CAPTURED)
            + "</div></div></div>"
        )
        fold = ""
    return (
        '<div class="phone-col" data-look="'
        + esc(verdict, 16)
        + '" data-key="'
        + esc(ident, 40)
        + '"><h4>'
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

#: A stored media file name, as this page may reference it. Names are produced
#: by ``media._safe`` (``A-Za-z0-9_.-``) and this is the CONSUMER side of that
#: one rule: a name that does not match is not referenced at all.
#:
#: Deliberately NOT ``report._slug``: that is a FUNCTION, not a compiled
#: pattern (``_SLUG`` is the pattern), and it rewrites ``.`` to ``-`` -- so
#: using it here would both raise ``AttributeError`` on every render that has a
#: media file and, once "fixed" by calling it, point every reference at a file
#: name that does not exist.
_MEDIA_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")


def _media_src(rec: object) -> str:
    """``media/<file>`` for a record with a usable file name, else ``""``."""
    body = rec if isinstance(rec, dict) else {}
    name = str(body.get("file") or "")
    if not name or ".." in name or not _MEDIA_NAME.match(name):
        return ""
    return MEDIA_DIR + "/" + name


def _media_key(value: object) -> str:
    """The ONE normalisation of a media-map key, used to BUILD the map and to
    read it.

    The producer stores ``tc_id`` as a plain string; ``_text`` collapses
    whitespace and appends an ellipsis past its limit. Keying with ``str(...)``
    on one side and ``_text(..., 40)`` on the other agrees for every id there is
    today and diverges the day one is longer or carries a space -- one fact
    derived twice in two places, which is how a mirrored derivation drifts. The
    derivation lives here; both sides call it.

    **The id shape this assumes, stated because the key TRUNCATES.** ``_text``
    caps at 40 characters, so two ids sharing a 40-character prefix collide and
    the later record wins the slot. That is safe for the ids this lane actually
    produces and for no others: a ``tc_id`` is a short case identifier
    (``TC-001``). Both sides truncate identically, so a collision can only ever
    show the WRONG clip for a case, never a broken reference -- and if an id
    shape that is long and prefix-shared is ever introduced, this limit is the
    line to change, in this one place.
    """
    return _text(value, 40)


def _media_map(cases: object) -> dict:
    """``{"clips": {tc_id: [rec, ...]}}``, derived ONCE and threaded down.

    **Clips only, and the frames are deliberately absent.** ``media`` writes a
    per-step FRAME record too, but this page already has one producer for "the
    device's own picture of this screen": :func:`_shot_html`, reading the
    per-observation PNGs :func:`_shots` funds, under the ambiguity rule
    :func:`_resolve_look` owns. A second frame path keyed on ``screen_id``
    would be a second answer to that one question -- and the wrong one on a
    conversation, where one ``screen_id`` covers several looks. So frames are
    read HERE by nobody, and the recording -- which no other producer has -- is
    what this map carries.

    Deriving it again inside a section would be two answers to one question,
    and mirrored derivations drift.
    """
    clips: dict = {}
    for case in list(cases or []):
        if not isinstance(case, dict):
            continue
        tc_id = _media_key(case.get("tc_id"))
        if not tc_id:
            continue
        for rec in list(case.get("media") or []):
            if not isinstance(rec, dict):
                continue
            if str(rec.get("kind") or "") == media.CLIP:
                clips.setdefault(tc_id, []).append(rec)
    for row in clips.values():
        row.sort(key=lambda rec: int(rec.get("seq") or 0))
    return {"clips": clips}


def _media_note(state: object) -> str:
    """The producer's own sentence for a state. NEVER this module's wording:
    ``media.NOTES`` is the one place a state is put into words, and a state
    with no entry is named rather than swallowed."""
    key = str(state or media.NOT_ATTEMPTED)
    return media.NOTES.get(key) or ("No clip for this step (" + esc(key, 40) + ").")


#: What a clip IS, as distinct from how it went.
#:
#: A SEPARATE note from :func:`_media_note`, and separate on purpose: "the
#: recorder refused this step" and "every clip on this page is smaller than the
#: screen was" are two different facts about the run, and one sentence carrying
#: both is the half-wired note this repository has already paid for. The same
#: three-note rule ``_shot_html`` follows for a missing picture and a dropped
#: one.
#:
#: MEASURED, not assumed (2026-09-10, emulator-5554, API 35): the device's
#: encoder refuses the panel's native 1440x3120 and silently retries at
#: 720x1280, so ``adb.SCREENRECORD_SIZE`` picks the size instead. The size is
#: NAMED rather than restated here, so this sentence cannot go stale the day
#: that constant changes.
CLIP_SCALE_NOTE = (
    "This clip was recorded at a reduced size, not at the resolution the device "
    "drew: the encoder refuses the screen's own size, so the lane chooses one "
    "(adb.SCREENRECORD_SIZE). It shows WHAT happened, not how sharp the app "
    "looked; the pictures and the element map on this page carry the detail."
)


def _video_html(tc_id: object, media_map: object) -> str:
    """Every clip of this case, in order, or the named reason for each gap.

    **The class is ``stepclip``, not ``clip``, and the name was checked before
    it was chosen.** ``.clip`` is ALREADY taken in ``report_shell.html``: it is
    the code-block truncation bar (and ``.clip.lazy`` beside it, which the
    shell's own JS selects by name), and ``tools/mobile_evidence/exchanges.py``
    emits ``<div class="clip">truncated ...</div>`` onto THIS SAME PAGE. A
    second ``.clip`` rule added below the first in the cascade would have
    repainted every one of those notes black and full-width. One name, one
    meaning, every consumer -- and the consumer here was another module's
    markup, which is why the check is a grep of the shell and not a memory of
    it. ``tests/mobile/test_mobile_report_css_binding.py`` asserts every class
    name this change introduces was UNSTYLED before it landed.
    """
    book = media_map if isinstance(media_map, dict) else {}
    rows = (book.get("clips") or {}).get(_media_key(tc_id)) or []
    if not rows:
        return ""
    out = []
    for rec in rows:
        if not isinstance(rec, dict):
            continue
        label = "Step " + str(int(rec.get("seq") or 0) + 1)
        srcpath = _media_src(rec)
        if srcpath:
            body = (
                '<video class="stepclip" controls preload="metadata" playsinline>'
                '<source src="' + esc(srcpath, 140) + '" type="video/mp4"></video>'
            )
        else:
            # NEVER an empty <video>: a player with no source reads as a broken
            # report, and the reason is the only thing worth showing here.
            body = ""
        # TWO notes, never one. The first says how this step's recording went;
        # the second says what every recording on this page IS. Collapsing them
        # would let a clean clip's caption read as if the downscale were a
        # failure, and a failed clip's caption claim a downscaled file exists.
        # The scale note is emitted ONLY where a clip actually plays: a step
        # with no file was not downscaled, it was not recorded.
        scale = (
            '<p class="stepclipnote">' + esc(CLIP_SCALE_NOTE, 300) + "</p>"
            if srcpath
            else ""
        )
        out.append(
            '<div class="stepcliprow"><h4>'
            + esc(label, 24)
            + "</h4>"
            + body
            + '<p class="stepclipnote">'
            + esc(_media_note(rec.get("state")), 300)
            + "</p>"
            + scale
            + "</div>"
        )
    return '<div class="stepclips">' + "".join(out) + "</div>"


def _seq_frames(rows: object, screens: object) -> dict:
    """``{trace index: frame html}`` for the merged app-evidence stream.

    The Run sequence has TWO renderers and only one of them is ``_seq_rows``.
    When a case has app evidence, ``_card_html`` renders
    ``ev_render.sequence_items(...)`` INSTEAD -- that stream REPLACES the seq
    rows whole -- so a picture threaded only into ``_seq_rows`` renders on no
    such run at all, while this page's own prose promises one for every step.
    Two render sites closed out of three is a promise that is false exactly
    where nobody looked.

    The indices are the ones ``sequence_items`` already aligns its trace against
    (``rows[i]``), so this is the SAME alignment rather than a second one.

    Built by :func:`_shot_html`, THE producer of this markup, and only on
    :func:`_resolve_look`'s ``LOOK_EXACT`` answer -- the same gate
    :func:`_phone_html` honours, so the merged stream cannot show one arbitrary
    look of an ambiguous screen as the step's own.
    """
    out: dict = {}
    for index, row in enumerate(list(rows or [])):
        if not isinstance(row, dict):
            continue
        ident, screen, verdict = _resolve_look(row.get("after"), screens)
        if screen is None or verdict != LOOK_EXACT:
            continue
        html = _shot_html(ident)
        if html:
            out[index] = html
    return out


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
        # Derived ONCE. The id that names a screen and the key that names a LOOK
        # at that screen must come from the same normalisation: a truncation on
        # one side and none on the other is two derivations of one id, which is
        # a defect the neighbouring plan for this file has already shipped.
        before_id = _text(safe.get("before_screen_id"), 40)
        after_id = _text(safe.get("after_screen_id"), 40)
        rows.append(
            {
                "index": _text(safe.get("index"), 8),
                "op": _text(action.get("op"), 24) or "?",
                "action": _action_line(safe.get("action")),
                "plain": _plain_action(action),
                "act": action,
                "target_id": _text(
                    (action.get("target") or {}).get("id")
                    if isinstance(action.get("target"), dict)
                    else "",
                    40,
                ),
                "outcome": _text(safe.get("outcome"), 40) or "-",
                "ms": _ms(safe.get("ms")),
                "detail": _text(safe.get("detail"), MAX_TEXT),
                "before": before_id,
                "after": after_id,
                # WHICH screen each end was, above; WHAT was on it, here. Two
                # questions, two names, and the key is minted by the STORE's own
                # producer so this reader cannot ask for a key the writer would
                # not have written. An observation id is 25 characters -- a
                # 12-hex screen id, a dash and 12 more -- so it survives the
                # 40-character `_text` a frame applies to it, with no room for
                # two observations to collide on a shared prefix.
                "before_obs": _obs_key(before_id, safe.get("before_screen_hash")),
                "after_obs": _obs_key(after_id, safe.get("after_screen_hash")),
            }
        )
    return rows


def _obs_key(ident: object, screen_hash: object) -> str:
    """The observation key for this end of a step, or ``""`` when there is none.

    NOT `run_store.observation_key` directly. That function degrades to the bare
    screen id when a screen carries no content hash, which is correct where it
    lives -- a hashless screen IS its own single observation, and the store must
    put it somewhere. At THIS boundary the degraded value is poison: it is
    truthy, so `_changed`'s "do both ends have an observation?" guard passes,
    and it is a screen id, so the comparison is then an observation key against
    an id -- two namespaces, silently.

    That is not a rare shape. `executor` stamps `before_screen_hash` on every
    entry but `after_screen_hash` at ONE of fourteen sites, so "before hashed,
    after not" is what thirteen of fourteen replay paths produce. Measured on a
    four-turn chat: every "Screen after" frame rendered the FULL final
    conversation, including a reply the app had not yet produced, captioned as
    what the action left on screen. Before this feature the frame was suppressed;
    drawing a fabricated one is strictly worse, and a tester cannot tell.

    So: no hash, no observation key, and every reader falls back to the screen id
    it used before. One producer, one meaning.
    """
    digest = str(screen_hash or "")
    if not digest:
        return ""
    return run_store.observation_key(ident, digest)


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


def _changed(row: dict) -> bool:
    """Did this action leave a DIFFERENT screen than it found?

    CONTENT when both ends recorded a content hash, identity otherwise. The id
    alone answers "a different screen", and on a chat every reply is the same
    screen with different content -- so the frame this used to suppress was the
    only evidence the reply ever arrived. The fallback is what a record written
    before observations existed still gets, unchanged.
    """
    if row["before_obs"] and row["after_obs"]:
        return row["after_obs"] != row["before_obs"]
    return row["after"] != row["before"]


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
        if row["after"] and _changed(row):
            detail += (
                '<div class="phonewide">'
                + _phone_html(
                    "Screen after",
                    "what this action left on screen",
                    row["after_obs"] or row["after"],
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


def _network_of(safe: dict) -> dict:
    """The case network capture record, or an empty dict. The ONE reader."""
    holder = safe.get("evidence") if isinstance(safe, dict) else None
    body = (holder or {}).get("network") if isinstance(holder, dict) else None
    return body if isinstance(body, dict) else {}


def _capture_of(run_id: str, tc_id: str) -> dict | None:
    """This case's redacted API-capture flows, read from evidence/. The ONE
    reader. ``None`` when nothing was ever written for this case -- a case a
    capture never touched, distinct from one it touched and found nothing on
    (which is a document carrying an empty ``flows`` list).

    Never raises: an evidence fault here can no more change a verdict than a
    failed log slice can.
    """
    if not run_id or not tc_id:
        return None
    try:
        read = run_store.read_evidence_text(run_id, tc_id, api_flows.EVIDENCE_NAME)
        text = read.get("content") if isinstance(read, dict) else None
        if not isinstance(text, str) or not text.strip():
            return None
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        logger.exception("mobile.report._capture_of failed for %s/%s", run_id, tc_id)
        return None


def _case_facts(case: object, manifest: dict, run_id: str = "") -> dict:
    """Everything a card, a table row, a chip and a KPI need, computed ONCE."""
    raw = case if isinstance(case, dict) else {}
    safe = run_store.redact(raw)
    safe = safe if isinstance(safe, dict) else {}
    tc_id = _text(safe.get("tc_id"), 40)
    rows = _trace_rows(safe.get("trace"))
    planned = _planned_case(manifest, tc_id)
    first_before = rows[0]["before"] if rows else _text(safe.get("screen_id"), 40)
    last_after = ""
    last_obs = ""
    for row in reversed(rows):
        if row["after"]:
            last_after = row["after"]
            last_obs = row["after_obs"]
            break
    # THE LAST LOOK THIS CASE ITSELF RECORDED, for the case whose final action
    # carries no after-hash -- which is what thirteen of `executor`'s fourteen
    # stamp sites produce. The alternative was an honest blank; a blank is worse
    # than the right picture here, because this case's own steps DID observe
    # that screen, so the card can show the turn the case actually ran on rather
    # than nothing. What it may never do is show a look this case never had,
    # which is why the scan is confined to these rows and matches on the screen
    # id -- a page-wide search would find the newest turn again, which is the
    # defect.
    last_look = last_obs
    if not last_look and last_after:
        for row in reversed(rows):
            if row["after"] == last_after and row["after_obs"]:
                last_look = row["after_obs"]
                break
            if row["before"] == last_after and row["before_obs"]:
                last_look = row["before_obs"]
                break
    # WHAT `last_look` IS, stated positively, because the name invites a wrong
    # reading and a disclaimer would not fix that:
    #
    #     the observation recorded by the LAST of this case's own rows that
    #     touched the screen the case ended on, scanning those rows from the
    #     end, taking an `after` observation in preference to a `before` one.
    #
    # It is NOT "the observation immediately before the final action". The scan
    # matches on the screen id, so a trailing row with an empty `after` whose
    # `before` names that same screen can supply it, and such a row may sit
    # later in the trace than the row that set `last_after`. That is harmless
    # for what this value is for -- every candidate is this case's own row at
    # the right screen, so it can never draw another case's turn or a later
    # turn of this one -- but a reader reconciling the card against the trace
    # should know which row they are looking at.
    #
    # Substituted means an EARLIER look at the right screen, so the caption must
    # say so rather than claim it is the end state. A record written before
    # observations existed substitutes nothing and keeps its old caption.
    last_substituted = bool(last_look) and last_look != last_obs
    # The observation each end WAS, kept beside the id each end IS. A card that
    # keys its frame on the id alone draws the last look at that screen, which
    # on a conversation is somebody else's turn.
    first_obs = rows[0]["before_obs"] if rows else ""
    screens_seen = {row["before"] for row in rows} | {row["after"] for row in rows}
    screens_seen.discard("")
    try:
        escapes = int(safe.get("escapes") or 0)
    except (TypeError, ValueError, OverflowError):
        escapes = 0
    try:
        free_stops = max(0, int(safe.get("free_stops") or 0))
    except (TypeError, ValueError, OverflowError):
        free_stops = 0
    wall = _case_wall(rows)
    return {
        "tc_id": tc_id,
        "slug": _slug(tc_id),
        "title": _text(safe.get("title"), 120),
        "verdict": _verdict_of(safe),
        # Read through the detector's own accessor, never by walking the record
        # here: two walks are two derivations of "where a crash lives".
        "crash": crash_detector.crash_of_case(safe),
        # The wire this case reached, read off the checkpoint through ONE
        # accessor. A second walk of the record inside the card would be a
        # second derivation of where the network record lives, and mirrored
        # conditions drift. Absent on a checkpoint written before this feature,
        # which is an empty dict the renderer states rather than a crash.
        "network": _network_of(safe),
        # This case's own API-capture flows (Phase 7, T7.1), read from
        # evidence/ -- never from the checkpoint, which only carries this
        # case's flow COUNT via ladder.record, not the flows themselves.
        "capture": _capture_of(run_id, tc_id),
        "status": _text(safe.get("status"), 40),
        "reason": _text(safe.get("reason"), 400),
        "finding": _text(safe.get("finding"), 200),
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
        "first_obs": first_obs,
        "last_obs": last_obs,
        "last_look": last_look,
        "last_substituted": last_substituted,
        "screens": len(screens_seen),
        "wall": wall,
        "lat": _lat_bucket(wall),
        "module": _text(planned.get("module"), 60) or "(unfiled)",
        "priority": _text(planned.get("priority"), 24) or "(unset)",
        "type": _text(planned.get("type"), 24) or "(unset)",
        "expected": _expected_of(planned),
    }


def _crash_block(crash: object) -> str:
    """The app's own death, as evidence rather than as a verdict.

    ``cerr`` is an EXISTING rule in ``report_shell.html`` -- mono, pre-wrap,
    defect-coloured, which is exactly a log excerpt. No new class name is
    introduced on purpose: this module emits class names that the shell must
    style, and a class with no rule renders unstyled with nothing failing.
    ``esc`` neutralises guard markers before escaping, so device text cannot
    forge prompt scaffolding on the page either.
    """
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
            + "</b>, so its own record is not a judgement"
        )
    return (
        '<div class="vstrip '
        + tone
        + '"><span class="label">Verdict</span>'
        + _pill(pill_cls, label.lower())
        + '<span class="vwhy">'
        + why
        + "</span></div>"
        + _crash_block(facts.get("crash"))
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
    case: object,
    screens: object,
    manifest: dict | None = None,
    app: str = "",
    media_map: object = None,
) -> str:
    """One case card in the shell's anatomy: expected -> verdict -> screens -> steps -> sequence -> ended at."""
    facts = _case_facts(case, manifest if isinstance(manifest, dict) else {})
    # The map is threaded here too rather than left at its default: this
    # wrapper builds a WHOLE case card, and the report's own "how to read"
    # prose promises a photograph in a case card. A default of None here would
    # make that sentence false for any caller who used this entry point.
    return _card_html(facts, screens, app, None, media_map)


def _card_html(
    facts: dict,
    screens: object,
    app: str,
    loaded: dict | None = None,
    media_map: object = None,
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
        # The kind, so the toolbar can filter on it and
        # `report_selfcheck._pin_crashes` can pin the page against the store.
        # Empty means no crash: falsy values are dropped from the attributes.
        "data-crash": str((facts.get("crash") or {}).get("kind") or ""),
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
    # THE SAME STEP LIST AS A JOURNEY TURN. Each step shows the screen before
    # and after it, resolved through `_look_pic`, so the ambiguous-look and
    # not-captured notes stand in place of a picture exactly as they do there.
    # The phone pair, the element maps and the lane-only sequence repeated those
    # same screens two and three times per card.
    #
    # THE END STATE KEEPS ITS SUBSTITUTION. When the last action's own after
    # look was not captured again, `_case_facts` names the last look this case
    # recorded at that screen, and the last step's After shows it -- with the
    # caption saying so, because the right picture under a caption claiming it
    # is the end state would be the same defect in better clothes.
    last = (
        (facts["last_look"], "the last look this case recorded at that screen")
        if facts.get("last_substituted") and facts.get("last_look")
        else ()
    )
    first_key = facts["first_obs"] or facts["first"]
    if rows:
        steps = (
            '<ol class="steplist">'
            + "".join(
                _step_html(
                    facts["slug"],
                    i,
                    row,
                    screens,
                    app,
                    last if i == len(rows) - 1 else (),
                )
                for i, row in enumerate(rows)
            )
            + "</ol>"
        )
    else:
        steps = (
            '<p class="empty-note big">No step has been replayed for this case yet.</p>'
            + (
                (
                    '<div class="steppics">'
                    + _look_pic(first_key, screens, "On screen", app)
                    + "</div>"
                )
                if first_key
                else ""
            )
        )
    # What the app heard and answered inside this case's window (plan P3), the
    # wire, and this case's own API capture -- folded, as in a Journey turn.
    # The network and capture blocks are NOT gated on `loaded`: the case runner
    # writes both onto the checkpoint, so a run with no app profile has them.
    merged = (
        ev_exchanges.seqlist(
            ev_render.sequence_items(
                loaded,
                facts["tc_id"],
                rows,
                _seq_frames(rows, screens),
            )
        )
        if loaded
        else ""
    )
    tech = (
        (ev_render.turns_table(loaded, facts["tc_id"]) if _app_logged(loaded) else "")
        + ev_render.case_network(facts.get("network"))
        + ev_render.case_capture(facts.get("capture"))
        + (
            _sec_block(
                "Run sequence",
                merged,
                count=len(rows) if rows else None,
                note="the app's records and the lane's actions on one clock",
            )
            if merged
            else ""
        )
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
        + _video_html(facts["tc_id"], media_map)
        + steps
        + _ended_block(facts)
        + '<details class="tech"><summary>Technical detail</summary>'
        + tech
        + "</details>"
        + "</div>\n</details>"
    )


# ── the sections ───────────────────────────────────────────────────────────────


def _app_label(manifest: dict) -> str:
    package = _text(manifest.get("package"), 80)
    return package or "(no app)"


def _locale_cell(manifest: dict) -> tuple | None:
    """The "what language did this run in?" fact, or None when unrecorded.

    None rather than "(unknown)" for every run written before this field
    existed, and for a device that would not answer: a cell claiming a language
    nobody recorded is worse than an absent one, which is the rule
    ``session.device_line`` already follows for the hardware kind.

    The VALUE is what the device said it was RENDERING in; the sub-line names
    what was asked for and how it got there. Those are different facts, and a
    run whose request did not take has to say so in the header a tester reads
    first -- with the ``gap`` tone, because it is one.
    """
    body = manifest.get("locale")
    record = body if isinstance(body, dict) else {}
    actual = str(record.get("actual") or "")
    requested = str(record.get("requested") or "")
    if not actual and not requested:
        return None
    if not requested:
        return ("language", esc(actual, 24), "the device was already in it", "")
    if not record.get("matched"):
        return (
            "language",
            esc(actual or "(not confirmed)", 24),
            esc(requested, 24) + " was asked for and the device did not take it",
            "gap",
        )
    return (
        "language",
        esc(actual, 24),
        "asked for "
        + esc(requested, 24)
        + (
            " and set at boot"
            if str(record.get("source") or "") == "boot"
            else " and set on the running device"
        ),
        "",
    )


#: The two kinds ``session.device_record`` writes, and the third state it leaves
#: EMPTY on purpose.
#:
#: That producer refuses to default an unknown kind to ``emulator`` because a
#: confident wrong answer about whether a run touched real hardware is worse than
#: a blank -- and this page then printed " · emulator" unconditionally, throwing
#: that care away and labelling every physical-device run an emulator. The
#: unknown case is now named rather than guessed.
#:
#: TWO SURFACES, TWO STRINGS, ONE STATE -- enumerated rather than asserted. This
#: page says "device kind unrecorded"; ``session.device_line`` says
#: "(kind not recorded for this run)" for the same empty kind. Nothing branches
#: on either, so this is an un-unified WORDING and not a half-wired sentinel --
#: but "one producer, one meaning" would be a false claim while a second wording
#: exists, so the pointer stands here instead of the claim. Unifying them is a
#: change to a different surface with its own tester-facing text.
DEVICE_KINDS = {"emulator": "emulator", "physical": "physical device"}
DEVICE_KIND_UNKNOWN = "device kind unrecorded"


def _device_sub(manifest: dict) -> str:
    """The device cell's sub-line: the avd, and what the manifest SAYS it ran on."""
    device = manifest.get("device")
    kind = str((device if isinstance(device, dict) else {}).get("kind") or "")
    return (
        esc(manifest.get("avd") or "(unknown avd)", 40)
        + " · "
        + DEVICE_KINDS.get(kind, DEVICE_KIND_UNKNOWN)
    )


def _facts_strip(
    run_id: str,
    manifest: dict,
    lease: dict,
    partial: bool,
    loaded: dict | None = None,
    coverage: dict | None = None,
) -> str:
    holder = _text(lease.get("session_id"), 40)
    displaced = _text(lease.get("taken_over_from"), 40)
    items = [
        (
            "device",
            esc(manifest.get("serial") or "(not attached)", 40),
            _device_sub(manifest),
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
        # TWO values, TWO names. The cell's VALUE is the run LIFECYCLE
        # (``partial``); its sub-line is VERDICT COVERAGE, which is a different
        # question -- a finished run can have reached no verdict at all, and
        # this cell used to claim the opposite in exactly that case. The tone
        # follows either being a gap, because either is.
        (
            "state",
            "in progress" if partial else "finished",
            esc(coverage_phrase(coverage), 200),
            "gap" if (partial or not (coverage or {}).get("complete")) else "ok",
        ),
    ]
    locale_cell = _locale_cell(manifest)
    if locale_cell:
        items.append(locale_cell)
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
        # A number without its word is a colour-coded riddle: a segment too
        # narrow for both says nothing, and the legend below names every count.
        if pct >= (len(label) + 3) * 1.6:
            text = "<b>" + str(n) + "</b>" + esc(label.lower(), 40)
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
        # The legend is the readable copy; the bar is its picture.
        '<div class="segbar" aria-hidden="true">'
        + "".join(segs)
        + '</div><ul class="seglegend">'
        + "".join(legend)
        + "</ul></figure>"
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
    facts: list,
    screens: object,
    app: str,
    loaded: dict | None = None,
    coverage: dict | None = None,
    media_map: object = None,
) -> str:
    cards = "".join(_card_html(f, screens, app, loaded, media_map) for f in facts)
    # NOT re-counted here. This lede and the four surfaces around it are ONE
    # number with one meaning, produced once by ``verdict_coverage``.
    lede = (
        str(len(facts))
        + " case"
        + ("" if len(facts) == 1 else "s")
        + " checkpointed — "
        + esc(coverage_phrase(coverage), 200)
        + ". Open a case for its screens, every action and the reason it "
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
    # The SECTION is opened by the seven-section assembly in `_document_body`; this
    # builder returns its BODY. It used to return a whole `<section id="cases">`, so
    # wrapping it produced TWO elements with id="cases" -- an ambiguous nav anchor and
    # invalid HTML, caught by the existing
    # test_a_slot_shaped_store_value_is_not_expanded, which counts that id.
    #
    # The count lede stays, as a paragraph inside the body: it carries
    # `coverage_phrase`, which is ONE number with one producer, and dropping it on the
    # way up would have made the section head the only count on the page.
    return '\n    <p class="lede">' + lede + "</p>\n" + body


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
        "<code>tools/mobile/report_shell.html</code>. This is one self-contained FOLDER: this page "
        "and the <code>media/</code> beside it, referenced relatively, so the folder can be zipped "
        "and opened offline anywhere."
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

    * an explore run that ``explore_runner.stop_reason`` does not call stopped
      is PARTIAL (``_is_partial``), so the reader is told the list is incomplete
      rather than shown a conclusion. NOT ``explore.stop`` alone, which is one
      of that producer's three clauses: a run whose deadline passed with no
      final turn has stopped, and reading the field said otherwise forever;
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
    except (TypeError, ValueError, OverflowError):
        replayed = 0
    # The CARD count under-counts turns: a turn whose script failed to parse
    # returns before it is checkpointed (``session._submit_explore``), while
    # ``explore_runner.next_turn`` had already incremented and persisted the
    # turn number -- so mrun-20260907-174354-758700 replayed 19 turns, wrote 17
    # cards, and this lede said "17 turns" with no silent turns at all.
    # The manifest's own counter is taken when it is LARGER; a smaller value
    # (an older build that never wrote one, a manifest not yet persisted) would
    # under-count in the other direction, and the lede must never claim fewer
    # turns than there are cards on the page.
    try:
        recorded = max(0, int(explore.get("turn") or 0))
    except (TypeError, ValueError, OverflowError):
        recorded = 0
    if recorded > replayed:
        replayed = recorded
    # Counted by DISTINCT turn, never by row. A turn resubmitted with a finding
    # appends a second row while leaving ONE checkpoint, so a row count read
    # "2 findings over 1 turn" and drove the silent count negative into its own
    # clamp -- which hid the miscount instead of reporting it.
    spoke = set()
    for note in notes:
        try:
            spoke.add(int(note.get("turn") or 0))
        except (TypeError, ValueError, OverflowError):
            continue
    silent = max(0, replayed - len(spoke))
    stop = _text(explore_stop(body), 40)
    # The run's TERMS, including every default the tester did not set: a
    # silent default is a run whose terms nobody can reconstruct afterwards.
    # `charter.describe_terms` is the ONE producer of this sentence, and it
    # returns "" for a run with no charter -- every run recorded before step 4
    # -- so those pages render exactly as they did.
    terms = charter_mod.describe_terms(explore.get("charter"))
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
        + ((" " + esc(terms, 800)) if terms else "")
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
        "findings", "What exploring found", "one line per turn", lede, table, level=3
    )


def _a11y_rows(screen_id: str, result: dict) -> str:
    """One screen's findings as table rows, or one honest row saying why not."""
    if not result.get("auditable"):
        return (
            '<tr><td class="num">'
            + esc(screen_id, 40)
            + '</td><td class="cap">nothing to audit</td><td dir="auto">'
            + esc(result.get("note") or screen_audit.NO_NODES_NOTE, 400)
            + "</td></tr>"
        )
    findings = [f for f in (result.get("findings") or []) if isinstance(f, dict)]
    if not findings:
        return (
            '<tr><td class="num">'
            + esc(screen_id, 40)
            + '</td><td class="cap">no finding</td><td dir="auto">'
            + esc(result.get("note") or "", 400)
            + "</td></tr>"
        )
    out = []
    for finding in findings:
        kind = str(finding.get("kind") or "")
        detail = str(finding.get("detail") or "")
        name = str(finding.get("name") or "")
        out.append(
            '<tr><td class="num">'
            + esc(screen_id, 40)
            + '</td><td class="cap">'
            + esc(screen_audit.KIND_LABELS.get(kind, kind), 80)
            + '</td><td dir="auto">'
            + (("<b>" + esc(name, 120) + "</b> — ") if name else "")
            + esc(detail, screen_audit.MAX_FINDING_CHARS)
            + ' <span class="cap">'
            + esc(" ".join(str(v) for v in (finding.get("ids") or [])), 120)
            + "</span></td></tr>"
        )
    return "".join(out)


def _a11y_section(screens: object) -> str:
    """What the run's own screens say about whether the app is USABLE.

    The findings come from ``screen_audit``, which ``perception.prune`` runs over
    every screen this lane ever pruned -- one producer, and this section reads the
    stored answer rather than deriving a second one.

    THREE outcomes that must never render alike, which is the whole point of the
    section: a screen with findings, a screen that was audited and had none, and a
    screen with no accessibility nodes AT ALL (a canvas app), where an empty list
    means nothing was looked at. A screen stored by a build older than the audit
    is a fourth: it carries no result, and this says so rather than counting it
    clean.

    Every interpolated value goes through :func:`esc` -- the labels are device
    text -- and only class names ``report_shell.html`` already styles are used.
    A finding NEVER touches a verdict: nothing here is read by
    ``_case_facts``, and the verdict producers do not import this module.
    """
    library = screens if isinstance(screens, dict) else {}
    audited = 0
    unauditable = 0
    unaudited = 0
    partial = 0
    assumed = 0
    total = 0
    rows = []
    for screen_id in sorted(str(key) for key in library.keys()):
        screen = library.get(screen_id)
        if not isinstance(screen, dict):
            continue
        result = screen.get("accessibility")
        if not isinstance(result, dict) or not result.get("present"):
            unaudited += 1
            rows.append(
                '<tr><td class="num">'
                + esc(screen_id, 40)
                + '</td><td class="cap">not audited</td><td dir="auto">'
                + "this screen was stored before the accessibility audit existed, "
                "so no finding here is evidence about it" + "</td></tr>"
            )
            continue
        if result.get("auditable"):
            audited += 1
            if not result.get("complete"):
                partial += 1
            if result.get("density_source") != screen_audit.DENSITY_DEVICE:
                assumed += 1
        else:
            unauditable += 1
        total += len([f for f in (result.get("findings") or []) if isinstance(f, dict)])
        rows.append(_a11y_rows(screen_id, result))
    if not rows:
        return ""
    lede = (
        str(total)
        + " finding"
        + ("" if total == 1 else "s")
        + " over "
        + str(audited)
        + " audited screen"
        + ("" if audited == 1 else "s")
        + ". These are usability defects, not functional ones: an accessibility "
        "finding NEVER changes a case's verdict, and no case here passed or "
        "failed because of one."
        + (
            " "
            + str(unauditable)
            + " screen(s) exposed no accessibility nodes at all, so they were not "
            "audited -- an empty list there means nothing was looked at, not that "
            "nothing is wrong."
            if unauditable
            else ""
        )
        + (
            " " + str(partial) + " audited screen(s) were PARTIAL: elements were "
            "dropped before the audit ran, so those rows describe part of a screen."
            if partial
            else ""
        )
        + (
            " " + str(unaudited) + " screen(s) carry no audit at all."
            if unaudited
            else ""
        )
        + (
            " On "
            + str(assumed)
            + " screen(s) the device did not report its display density, so any "
            "touch-target finding there rests on an ASSUMED density and can be a "
            "false alarm on low-density hardware -- each such finding says so."
            if assumed
            else ""
        )
        + " Colour contrast, jank and start-up time are NOT checked here."
    )
    table = (
        '<div class="tablewrap"><table class="cov"><thead><tr>'
        '<th scope="col" class="num">Screen</th>'
        '<th scope="col">What</th>'
        '<th scope="col">Detail</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )
    return _sechead(
        "a11y",
        "Accessibility",
        "what the screens themselves say",
        lede,
        table,
        level=3,
    )


def _cases_html(
    facts: list,
    screens: object,
    app: str,
    loaded: dict | None,
    coverage: dict | None,
    observations: object,
    media_map: object = None,
) -> str:
    """The cases section, plus the store's own disclosure when the run reached
    its observation limit.

    Derived HERE and nowhere else. A truncated evidence set that renders as a
    complete one is the failure this whole change exists to end, so the page
    says the limit was reached rather than letting a frame read as "not
    captured" for a reason nobody can see.
    """
    body = _cases_section(facts, screens, app, loaded, coverage, media_map)
    kept = len(observations) if isinstance(observations, dict) else 0
    if kept >= run_store.MAX_RUN_OBSERVATIONS:
        # CONDITIONAL, because this function cannot tell whether anything was
        # actually dropped. It sees how many observations were KEPT, and a run
        # that ends exactly at the limit lost nothing -- the earlier wording
        # said "a screen was not stored" and told such a tester their evidence
        # was incomplete when it was whole. Saying what FOLLOWS from the limit
        # is true in both cases; claiming a loss is only true in one.
        body += (
            '<p class="hint">This run reached the store\'s limit of '
            + str(run_store.MAX_RUN_OBSERVATIONS)
            + " kept screen observations. Any screen first seen after that point "
            "is not stored, and its frame reads as not captured.</p>"
        )
    return body


def _merged_library(screens: object, observations: object) -> dict:
    """The one book a FRAME resolves against: every screen and every look at it.

    TWO libraries, joined once, HERE. ``screens`` answers which screens the run
    visited and stays the accessibility audit's input -- one row per screen, not
    one per look at it. Adding every stored OBSERVATION is what lets a four-turn
    conversation draw four pictures instead of one.

    Named rather than inlined because two consumers now need the SAME book: the
    page body resolves frames against it and :func:`_shots` funds pictures for
    it, and a picture funded against one book and resolved against another is a
    caption with no image under it.

    Merged rather than passed as a pair because the keys do not collide IN
    PRACTICE: ``perception._screen_id`` emits twelve hex characters and an
    observation id is that id plus a dash and up to twelve more, so no screen id
    has the shape of an observation key. NOT an invariant the code enforces:
    ``run_store._RUN_ID_RE`` accepts up to 64 characters of ``[A-Za-z0-9._-]``,
    so a STORED screen id of ``aabbccdd0011-000000000000`` is admissible and
    would collide, with the observation silently winning this ``update``.
    Nothing produces such an id today; if something ever does, the fix is two
    dictionaries rather than a longer comment. Stated because an invariant
    asserted in prose and unenforced in code is how a reader stops checking.
    """
    library = dict(screens if isinstance(screens, dict) else {})
    library.update(observations if isinstance(observations, dict) else {})
    return library


def _document(**kwargs) -> str:
    """The page, with this run's inlined frames funded ONCE for the whole build.

    A wrapper rather than a parameter on every builder: see :data:`_SHOTS`. The
    signature is unchanged for every caller, and the book is RESET in ``finally``
    so one report's frames can never be read by the next.

    Funded against :func:`_merged_library` -- the same call the body makes -- so
    the keys this pays for are exactly the keys the frames resolve on.
    """
    token = _SHOTS.set(
        _shots(
            str(kwargs.get("run_id") or ""),
            _merged_library(kwargs.get("screens"), kwargs.get("observations")),
        )
    )
    images = _IMAGES.set(_image_book(kwargs.get("run_id") or ""))
    secrets = _SECRETS.set(_run_secrets(kwargs.get("manifest"), kwargs.get("cases")))
    try:
        return _document_body(**kwargs)
    finally:
        _SECRETS.reset(secrets)
        _IMAGES.reset(images)
        _SHOTS.reset(token)


#: The sentence an OLD manifest gets. A run created before this field existed genuinely
#: does not know its image, and the page says exactly that. It NEVER falls back to
#: ``settings`` or to a live ``sdk_locator.avd_system_image()`` read: both answer with
#: TODAY's image, which is a fabricated fact about a finished run -- the
#: success-shaped absence this repository has paid for before.
SYSTEM_IMAGE_ABSENT = (
    "not recorded for this run: it was created before the image was snapshotted into "
    "the manifest, and today's setting is not evidence of what ran then"
)

#: Every environment fact this page can state, and the named reason for each it cannot.
#: One row per fact, and a row is NEVER omitted when the value is missing -- an omitted
#: row reads as "nothing to say", which is the success shape.


def _env_section(manifest: dict, loaded: dict | None = None) -> str:
    """The Environment & provenance section body.

    Reads the manifest and ``platform_info.SUPPORT``; DERIVES nothing that a producer
    already owns. The system image comes from the manifest SNAPSHOT and from nowhere else.
    """
    book = manifest if isinstance(manifest, dict) else {}
    rows = []

    def row(label, value, sub=""):
        rows.append(
            '<div class="envrow"><span class="rfl">'
            + esc(label, 60)
            + '</span><span class="rfv">'
            + value
            + ("<small>" + esc(sub, 300) + "</small>" if sub else "")
            + "</span></div>"
        )

    image = str(book.get("system_image") or "")
    row(
        "System image",
        esc(image, 120)
        if image
        else '<span class="cap none">' + esc(SYSTEM_IMAGE_ABSENT, 300) + "</span>",
        "snapshotted at run creation from sdk_locator.avd_system_image(); HTTPS body "
        "decryption is only possible on a rootable image"
        if image
        else "",
    )
    row("Device", esc(_device_sub(book), 120))
    # platform_info has NO `host_os`, and `raw_platform()` returns sys.platform
    # ("darwin"), which is NOT a SUPPORT key -- its own docstring says it is for
    # REPORTING the host, never for branching. So the host is NAMED from
    # raw_platform() and the support claim is DERIVED by support_statement(),
    # which is the module's single producer of that sentence: promoting Windows
    # without a real run changes this row and the three docs together, or the
    # existing platform-support pin names each one.
    host = (
        platform_info.MACOS
        if platform_info.is_macos()
        else (platform_info.WINDOWS if platform_info.is_windows() else "")
    )
    row(
        "Host platform",
        esc(platform_info.raw_platform() or "(unknown)", 60),
        platform_info.support_statement(host),
    )
    row(
        "Rendered",
        esc(_short_stamp(time.time()), 40),
        "when this page was BUILT, which is not when the run happened",
    )
    return '<div class="runstrip">' + "".join(rows) + "</div>"


#: The run's last known moment, DERIVED, and labelled as derived everywhere it is shown.
#:
#: The manifest carries ``created`` and NO ``ended``: nothing writes one, because a run can
#: be abandoned and an abandoned run has no honest end. So this page never prints the word
#: "ended". What it can say is when the newest case was last checkpointed, which is a
#: different fact with a different name -- and one producer for it, so the masthead, the
#: overview strip and the timeline cannot disagree about when the run stopped.


def _epoch(value: object) -> float | None:
    """An epoch SECONDS stamp from a store field, or ``None`` when it is not one.

    Deliberately NOT :func:`_ms`, which is a DURATION coercer: it clamps to ``10**9``, and
    every real epoch second passed ``10**9`` in 2001 -- so reading a timestamp through it
    pins every stamp on this page to the same instant in 2001, and the caller that then
    divided by 1000 moved the run to 1970. Two errors that agree, and a fixture whose
    ``updated`` is a small number (2.0, 300.0) cannot see either one. That is why
    ``test_a_real_epoch_stamp_is_not_clamped`` grades this with a REAL stamp.
    """
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or number in (float("inf"), float("-inf")) or number <= 0:
        return None
    return number


def last_checkpoint(cases: object) -> float | None:
    """The newest ``case["updated"]`` in epoch SECONDS, or ``None`` when none carries one."""
    stamps = []
    for case in cases if isinstance(cases, (list, tuple)) else ():
        value = _epoch(case.get("updated")) if isinstance(case, dict) else None
        if value is not None:
            stamps.append(value)
    return max(stamps) if stamps else None


#: The word the page uses for it, so no section invents its own.
LAST_CHECKPOINT_LABEL = "last checkpoint"
LAST_CHECKPOINT_SUB = "derived from the newest case checkpoint; this run recorded no end time, because nothing writes one and an abandoned run has no honest end"


def elapsed_note(manifest: object, cases: object) -> str:
    """Elapsed, or the NAME of whichever half is missing. Never a silent blank."""
    book = manifest if isinstance(manifest, dict) else {}
    created = _epoch(book.get("created"))
    end = last_checkpoint(cases)
    if created is None and end is None:
        return "no start time recorded, and no case has ever been checkpointed"
    if created is None:
        return "no start time recorded, so elapsed cannot be computed"
    if end is None:
        return "no case has been checkpointed yet, so elapsed cannot be computed"
    # Both halves are epoch SECONDS; fmt_ms wants milliseconds.
    return fmt_ms(max(0, int((end - created) * 1000.0)))


def error_classes(facts: object) -> dict:
    """``{outcome: count}`` over every trace entry of every case. ONE derivation.

    The overview KPI, the Logs breakdown and any case badge all read THIS dict. A section
    that counted its own rows would be a second derivation of one answer, and mirrored
    conditions drift -- which is why the pin monkeypatches this function and requires BOTH
    consuming sections to move.

    Only outcomes in ``executor.DEVICE_ERROR_OUTCOMES`` are counted. An outcome minted
    there and missing from that set fails the source scan in the pin, not silently here.
    """
    counts: dict = {}
    for fact in facts if isinstance(facts, (list, tuple)) else ():
        rows = (fact or {}).get("rows") if isinstance(fact, dict) else None
        for row in rows if isinstance(rows, (list, tuple)) else ():
            outcome = (
                str((row or {}).get("outcome") or "") if isinstance(row, dict) else ""
            )
            if outcome in executor.DEVICE_ERROR_OUTCOMES:
                counts[outcome] = counts.get(outcome, 0) + 1
    return counts


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


def _logs_section(manifest: object, facts: object, turns: int) -> str:
    """Findings, the explore-lane stop reason, and the device-error breakdown.

    The breakdown reads :func:`error_classes` -- the SAME call the overview KPI reads. It
    does not count rows of its own; two derivations of one number drift, and the pin
    monkeypatches the producer and requires both to move.
    """
    book = manifest if isinstance(manifest, dict) else {}
    body = _findings_section(book, turns) or ""
    stop = explore_stop(book)
    if stop:
        body += '<div class="guard"><p class="gapterm">why this run stopped</p>'
        body += '<p class="gsub">' + stop + "</p></div>"
    body += (
        '<div class="card"><p class="elab">device errors by class</p>'
        '<p class="logline">'
        + esc(error_phrase(error_classes(facts)), 300)
        + "</p></div>"
    )
    return body


#: The page's OWN limits, named. Each is a fact about what this report cannot show, and
#: each is here rather than absent because an absent limit reads as no limit.
#:
#: The first three are the gaps this change deliberately did NOT close -- see
#: docs/DECISIONS.md. Naming them on the page is the whole point: a tester who cannot see
#: a requirement id must be told no case record carries one, not left to conclude the run
#: had no requirements.
PAGE_LIMITS = (
    (
        "No requirement id per case",
        "No case record on disk carries one. Suite-lane intake would have to start carrying it; until then this page cannot trace a case to a requirement.",
    ),
    (
        "No per-step offset inside a clip",
        "media.finish_step writes one clip per submit and records no offset per action, so this page cannot point at the moment a step happened inside a recording.",
    ),
    (
        "No SDK, platform-tools, emulator or backend version",
        "The manifest stores none and this module reads none. The system image is the one provisioning fact that IS recorded.",
    ),
    (
        "This page cannot show that a case is CORRECT",
        "Every figure here is about what the emulator DID. Whether the app is right is the tester's question.",
    ),
    (
        "Calls the capture did not see are not here",
        "A request that never reached the proxy leaves no row, and an empty table is not evidence of an idle app.",
    ),
    (
        "No bodies below the decrypted tier",
        "Which tier a run reached is stated in the network section; below it, headers and bodies were never readable.",
    ),
)


# ── the run page: header, issues, journey, scripted cases, diagnostics ─────────


#: Section ids, top to bottom. The nav lists only the ones this run fills.
ISSUES_ID = "issues"
JOURNEY_ID = "journey"
CASES_ID = "cases"
DIAG_ID = "diagnostics"
ABOUT_ID = "about"

#: This build's picture book: where the report's media and the run's shots
#: live, the derivatives made so far, how many images were emitted (the first
#: loads eagerly) and every image note Diagnostics must print. Set by
#: :func:`_document` and reset in its ``finally``, like :data:`_SHOTS`.
_IMAGES: ContextVar = ContextVar("_IMAGES", default=None)

#: ``sizes`` for a step picture and for a filmstrip thumbnail.
STEP_SIZES = "(max-width: 600px) 44vw, 240px"
THUMB_SIZES = "(max-width: 600px) 30vw, 160px"

#: A trace outcome in plain words. Anything unlisted reads as its own name.
OUTCOME_WORDS = {
    "ok": "done",
    "done": "finished",
    "assert_pass": "check passed",
    "assert_fail": "check failed",
    "visual_check": "visual check (judged by the model)",
    "left_app": "left the app",
    "route_mismatch": "screen not as saved",
    "supplied": "tester answered",
    "-": "not run",
}

#: The ops whose target is an element on the screen before the step.
TARGET_OPS = ("tap", "long_press", "type", "clear")


def _image_book(run_id: object) -> dict:
    target = report_path(str(run_id))
    return {
        "media_dir": target.parent / MEDIA_DIR,
        "shots_dir": target.parent.parent / run_store.SHOTS_DIR,
        "variants": {},
        "notes": [],
        "seen": 0,
    }


def _image_note(text: object) -> None:
    book = _IMAGES.get()
    if book is not None and text and str(text) not in book["notes"]:
        book["notes"].append(str(text))


def _variants(path: Path) -> dict:
    """``report_images.ensure_variants`` once per file per build."""
    book = _IMAGES.get()
    if book is None:
        return {}
    key = str(path)
    if key not in book["variants"]:
        got = report_images.ensure_variants(path, book["media_dir"])
        _image_note(got.get("error"))
        book["variants"][key] = got
    return book["variants"][key]


def _img_html(rel_dir: str, name: str, path: Path, alt: str, sizes: str) -> str:
    """One ``<img>`` with its WebP ``srcset``; ``data-full`` is the original.

    Every value is escaped by :func:`esc`, inside ``srcset_markup`` or here.
    The first image of the page loads eagerly; every other one is lazy.
    """
    book = _IMAGES.get()
    got = _variants(path)
    # Derivatives always live in the report's media folder; the original
    # stays where it is (a screenshot sits in ../shots beside the report).
    marks = report_images.srcset_markup(
        esc,
        MEDIA_DIR,
        got.get("hash"),
        got.get("widths"),
        name,
        got.get("width"),
        original_dir=rel_dir,
    )
    _image_note(marks.get("error"))
    first = book is not None and not book["seen"]
    if book is not None:
        book["seen"] += 1
    width, height = got.get("width"), got.get("height")
    return (
        '<img src="'
        + marks["src"]
        + '"'
        + (
            (' srcset="' + marks["srcset"] + '" sizes="' + esc(sizes, 80) + '"')
            if "," in marks["srcset"]
            else ""
        )
        + (
            (' width="' + str(int(width)) + '" height="' + str(int(height)) + '"')
            if width and height
            else ""
        )
        + (' fetchpriority="high"' if first else ' loading="lazy"')
        + ' decoding="async" alt="'
        + esc(alt, 160)
        + '" data-full="'
        + esc(rel_dir.rstrip("/") + "/" + name, 200)
        + '">'
    )


def _q(text: object) -> tuple:
    """A quoted run of app text inside a plain-words line."""
    return ("q", _text(text, 80))


def _rid_tail(rid: object) -> str:
    return str(rid or "").rsplit("/", 1)[-1].replace("_", " ")


def _plain_action(action: object) -> list:
    """A trace action as plain words: text parts and :func:`_q` quotes.

    Typed text goes through :func:`_typed_literal`, so a credential is the
    mask here exactly as it is in the step table.
    """
    body = action if isinstance(action, dict) else {}
    op = str(body.get("op") or "")
    target = body.get("target")
    target = target if isinstance(target, dict) else {}
    name = _text(
        target.get("text")
        or target.get("label")
        or target.get("desc")
        or _rid_tail(target.get("rid")),
        60,
    )
    role = _text(target.get("role"), 24)
    thing = [_q(name)] if name else ["the " + role if role else "an unlabelled element"]
    if op == "tap":
        return ["Tapped "] + thing
    if op == "long_press":
        return ["Long-pressed "] + thing
    if op == "clear":
        return ["Cleared "] + thing
    if op == "type":
        typed = _typed_literal(body)
        return ["Typed ", _q(typed)] + ([" into "] + thing if name or role else [])
    if op in ("scroll", "swipe"):
        return ["Scrolled " + (_text(body.get("dir"), 12) or "the screen")]
    if op in ("back", "home"):
        return ["Pressed " + op.capitalize()]
    if op == "wait":
        until = _text(body.get("until_text"), 80)
        if until:
            return ["Waited for ", _q(until), " to appear"]
        return ["Waited " + fmt_ms(_ms(body.get("ms")) or 0)]
    if op == "wait_until_text":
        return ["Waited for ", _q(_text(body.get("text"), 80)), " to appear"]
    if op == "wait_until_gone":
        return ["Waited for ", _q(_text(body.get("text"), 80)), " to go"]
    if op == "wait_until_changed":
        return ["Waited for the screen to change"]
    if op == "wait_until_idle":
        return ["Waited for the screen to settle"]
    if op == "assert":
        text = _text(body.get("text") or body.get("contains"), 80)
        return (
            ["Checked that ", _q(text), " is on screen"]
            if text
            else ["Checked the screen"]
        )
    if op == "done":
        reason = _text(body.get("reason"), 200)
        return ["Finished the turn" + (": " if reason else "")] + (
            [_q(reason)] if reason else []
        )
    if op == "ask_tester":
        field = _text(body.get("field"), 24)
        return ["Asked the tester for " + (field or "input")]
    if op == "launch":
        return ["Opened the app"]
    return [_action_line(body)]


def _parts_html(parts: object) -> str:
    out = []
    for part in list(parts or []):
        if isinstance(part, tuple):
            out.append('<q dir="auto">' + esc(part[1], 200) + "</q>")
        else:
            text = str(part)
            out.append(
                (" " if text[:1] == " " else "")
                + esc(text, 200)
                + (" " if text[-1:] == " " else "")
            )
    return "".join(out)


def _element_of(target_id: object, screen: object) -> dict:
    tid = str(target_id or "")
    body = screen if isinstance(screen, dict) else {}
    for element in list(body.get("elements") or []) if tid else []:
        if isinstance(element, dict) and str(element.get("id") or "") == tid:
            return element
    return {}


def _step_words(row: dict, library: object) -> list:
    """The row's plain words, naming its target from the before screen when
    the trace itself recorded only the element's id."""
    action = row.get("act") if isinstance(row.get("act"), dict) else {}
    target = action.get("target") if isinstance(action.get("target"), dict) else {}
    if not target or any(target.get(k) for k in ("text", "label", "desc", "rid")):
        return row.get("plain") or []
    _ident, screen, _verdict = _resolve_look(
        row.get("before_obs") or row.get("before"), library
    )
    element = _element_of(target.get("id"), screen)
    if not element:
        return row.get("plain") or []
    named = {k: element.get(k) for k in ("text", "label", "desc", "rid", "role")}
    return _plain_action(dict(action, target=dict(target, **named)))


def _tap_overlay(target_id: object, screen: object) -> str:
    """The acted-on element's box and a tap dot, in CSS percentages, or ``""``."""
    tid = str(target_id or "")
    body = screen if isinstance(screen, dict) else {}
    if not tid:
        return ""
    for element in list(body.get("elements") or []):
        if isinstance(element, dict) and str(element.get("id") or "") == tid:
            dev_w, dev_h = _device_size(body)
            box = report_images.overlay_style(_bounds_of(element), dev_w, dev_h)
            if not box:
                return ""
            return (
                '<span class="tapbox" aria-hidden="true" style="left:{left}%;top:{top}%;'
                'width:{width}%;height:{height}%"></span><span class="tapdot" '
                'aria-hidden="true" style="left:{dot_x}%;top:{dot_y}%"></span>'
            ).format(**{k: float(v) for k, v in box.items()})
    return ""


def _look_pic(
    key: object,
    library: object,
    caption: str,
    app: str,
    target_id: str = "",
    note: str = "",
) -> str:
    """One moment of a step: the device's picture, the drawing, or why neither.

    The note stands IN PLACE of a picture, never beside one. The drawing is
    composed only when no picture was stored for an exact look.

    Every figure is stamped with what `_resolve_look` returned: ``data-look``
    the verdict and ``data-screen`` the key it resolved. The page-level sweep in
    ``test_mobile_screen_observations`` reads those, so a caller that resolved
    the library itself would be graded on the day it was written.
    """
    ident, screen, verdict = _resolve_look(key, library)
    head = (
        '<figure class="pic" data-look="'
        + esc(verdict, 16)
        + '"'
        + ((' data-screen="' + esc(ident, 40) + '"') if ident else "")
        + ">"
    )
    label = esc(caption, 40) + (
        ('<span class="picsub">' + esc(note, 80) + "</span>") if note else ""
    )
    if screen is not None and verdict == LOOK_EXACT:
        filename = str(((_SHOTS.get() or {}).get("frames") or {}).get(ident) or "")
        book = _IMAGES.get()
        if filename and book is not None:
            return (
                head
                + '<div class="picbox">'
                + _img_html(
                    "../" + run_store.SHOTS_DIR,
                    filename,
                    book["shots_dir"] / filename,
                    caption + ": the screen as the device drew it",
                    STEP_SIZES,
                )
                + _tap_overlay(target_id, screen)
                + "</div><figcaption>"
                + label
                + "</figcaption></figure>"
            )
        return (
            head
            + '<div class="picdraw">'
            + screen_phone.compose(screen, esc=esc, app=app)
            + "</div><figcaption>"
            + label
            + " · drawn from the element list, no picture stored"
            + "</figcaption></figure>"
        )
    gap = AMBIGUOUS_LOOK if verdict == LOOK_AMBIGUOUS else NOT_CAPTURED
    return (
        head
        + '<p class="picnote">'
        + esc(gap, 300)
        + "</p><figcaption>"
        + label
        + "</figcaption></figure>"
    )


def _same_gap(one: tuple, two: tuple) -> bool:
    """Both looks resolved to no picture, with the same verdict."""
    return one[2] == two[2] and one[2] != LOOK_EXACT


def _step_html(
    tc_slug: str,
    index: int,
    row: dict,
    library: object,
    app: str,
    after_look: tuple = (),
) -> str:
    """One step: its words, its outcome, what the server said about it, and
    the screen before and after.

    *after_look* is ``(key, note)`` when the caller knows a better After than
    the row's own key: the case card's substituted end state, disclosed in
    *note*.
    """
    outcome = str(row.get("outcome") or "-")
    word = OUTCOME_WORDS.get(outcome.lower(), outcome.replace("_", " "))
    before = row.get("before_obs") or row.get("before")
    after = row.get("after_obs") or row.get("after")
    target = row.get("target_id") if row.get("op") in TARGET_OPS else ""
    if after_look:
        pics = (_look_pic(before, library, "Before", app, target) if before else "") + (
            _look_pic(after_look[0], library, "After", app, note=after_look[1])
        )
    elif before and (
        before == after
        or (
            after
            and _same_gap(_resolve_look(before, library), _resolve_look(after, library))
        )
    ):
        # Neither end has a picture and both miss it for the same reason: one
        # note for the pair, not the same sentence twice side by side.
        pics = _look_pic(before, library, "Before and after", app, target)
    else:
        pics = (_look_pic(before, library, "Before", app, target) if before else "") + (
            _look_pic(after, library, "After", app) if after else ""
        )
    detail = str(row.get("detail") or "")
    return (
        '<li class="step" id="step-'
        + tc_slug
        + "-"
        + str(int(index))
        + '"><div class="stephead"><span class="stepn">'
        + str(int(index) + 1)
        + '</span><p class="stepact" dir="auto">'
        + _parts_html(_step_words(row, library))
        + "</p>"
        + _outcome_pill(word)
        + "</div>"
        + (
            ('<p class="stepnote" dir="auto">' + esc(detail, 300) + "</p>")
            if detail
            else ""
        )
        + (('<div class="steppics">' + pics + "</div>") if pics else "")
        + "</li>"
    )


def _clip_html(rec: object) -> str:
    """A turn's recording with its poster, or the producer's reason for none."""
    body = rec if isinstance(rec, dict) else {}
    srcpath = _media_src(body)
    if not srcpath:
        return '<p class="clipgap">' + esc(_media_note(body.get("state")), 300) + "</p>"
    poster = ""
    book = _IMAGES.get()
    if book is not None:
        got = report_images.ensure_poster(
            book["media_dir"] / str(body.get("file")), book["media_dir"]
        )
        _image_note(got.get("error"))
        if got.get("path"):
            poster = ' poster="' + esc(MEDIA_DIR + "/" + str(got["path"]), 200) + '"'
    return (
        '<figure class="turnclip"><video controls preload="metadata" playsinline'
        + poster
        + '><source src="'
        + esc(srcpath, 140)
        + '" type="video/mp4"></video><figcaption>Recording of this turn</figcaption></figure>'
    )


def _turn_frame(tc_id: object, cases_by_id: dict) -> dict:
    """The turn's full-size frame record from the media lane, or ``{}``."""
    case = cases_by_id.get(_media_key(tc_id)) or {}
    for rec in list(case.get("media") or []):
        if (
            isinstance(rec, dict)
            and str(rec.get("kind") or "") == media.FRAME
            and _media_src(rec)
        ):
            return rec
    return {}


def _thumb_html(n: int, f: dict, frame: dict, library: object) -> str:
    book = _IMAGES.get()
    img = ""
    if frame and book is not None:
        name = str(frame.get("file"))
        img = _img_html(
            MEDIA_DIR, name, book["media_dir"] / name, "Turn " + str(n), THUMB_SIZES
        )
    elif book is not None:
        ident, screen, verdict = _resolve_look(f["last_look"] or f["last"], library)
        filename = str(((_SHOTS.get() or {}).get("frames") or {}).get(ident) or "")
        if screen is not None and verdict == LOOK_EXACT and filename:
            img = _img_html(
                "../" + run_store.SHOTS_DIR,
                filename,
                book["shots_dir"] / filename,
                "Turn " + str(n),
                THUMB_SIZES,
            )
    return (
        '<li><a class="thumb" href="#turn-'
        + f["slug"]
        + '">'
        + (img or '<span class="thumbgap">no picture</span>')
        + "<span>Turn "
        + str(int(n))
        + "</span></a></li>"
    )


def _finding_of(f: dict, manifest: dict, n: int) -> str:
    if f.get("finding"):
        return f["finding"]
    explore = (
        manifest.get("explore") if isinstance(manifest.get("explore"), dict) else {}
    )
    for item in list(explore.get("findings") or []):
        if isinstance(item, dict) and str(item.get("turn")) == str(n):
            return _text(item.get("note"), 200)
    return f["title"] or f["tc_id"]


def _result_pill(word: str) -> str:
    return _pill(
        RESULT_PILL.get(word, "p-void"),
        RESULT_LABEL.get(word, word),
    )


def _count(n: int, one: str, many: str = "") -> str:
    return str(int(n)) + " " + (one if int(n) == 1 else (many or one + "s"))


def _turn_html(
    n: int,
    f: dict,
    manifest: dict,
    library: object,
    app: str,
    media_map: object,
    loaded: dict | None,
) -> str:
    verdict = f["verdict"]
    word = _unify_verdict(verdict)
    pill = _result_pill(word) if verdict in DONE_VERDICTS else ""
    rows = f["rows"]
    steps = "".join(
        _step_html(f["slug"], i, row, library, app) for i, row in enumerate(rows)
    )
    clips = "".join(
        _clip_html(rec)
        for rec in ((media_map or {}).get("clips") or {}).get(_media_key(f["tc_id"]))
        or []
    )
    meta = [_count(len(rows), "step")]
    if f["wall"] is not None:
        meta.append(fmt_ms(f["wall"]))
    tech = [
        ("Case", f["tc_id"]),
        ("Status", f["status"] or "(none)"),
        ("Why it ended", f["reason"]),
    ]
    tech_rows = "".join(
        "<dt>" + esc(k, 40) + '</dt><dd dir="auto">' + esc(v, 400) + "</dd>"
        for k, v in tech
        if v
    )
    extra = (
        _crash_block(f.get("crash"))
        + (ev_render.turns_table(loaded, f["tc_id"]) if _app_logged(loaded) else "")
        + ev_render.case_network(f.get("network"))
        + ev_render.case_capture(f.get("capture"))
    )
    return (
        '<article class="turn" id="turn-'
        + f["slug"]
        + '" data-tc="'
        + esc(f["tc_id"], 40)
        + '" data-verdict="'
        + esc(verdict, 24)
        + '" data-crash="'
        + esc(str((f.get("crash") or {}).get("kind") or ""), 40)
        + '"><header class="turnhead"><span class="turnn">Turn '
        + str(int(n))
        + '</span><h4 dir="auto">'
        + esc(_finding_of(f, manifest, n), 200)
        + "</h4>"
        + pill
        + '<span class="turnmeta">'
        + esc(" · ".join(meta), 60)
        + "</span></header>"
        + clips
        + (
            ('<ol class="steplist">' + steps + "</ol>")
            if steps
            else '<p class="picnote">No step was replayed in this turn.</p>'
        )
        + '<details class="tech"><summary>Technical detail</summary><dl class="techlist">'
        + tech_rows
        + "</dl>"
        + extra
        + "</details></article>"
    )


def _journey_html(
    groups: list,
    facts_by_id: dict,
    cases_by_id: dict,
    manifest: dict,
    library: object,
    app: str,
    media_map: object,
    loaded: dict | None,
) -> str:
    order = {tc: i + 1 for i, tc in enumerate(facts_by_id)}
    out = []
    for gi, group in enumerate(groups, 1):
        facts = [
            facts_by_id[_text(c.get("tc_id"), 40)]
            for c in group["cases"]
            if _text(c.get("tc_id"), 40) in facts_by_id
        ]
        if not facts:
            continue
        steps = sum(len(f["rows"]) for f in facts)
        walls = [f["wall"] for f in facts if f["wall"] is not None]
        meta = [_count(len(facts), "turn"), _count(steps, "step")]
        if walls:
            meta.append(fmt_ms(sum(walls)))
        film = "".join(
            _thumb_html(
                order[f["tc_id"]], f, _turn_frame(f["tc_id"], cases_by_id), library
            )
            for f in facts
        )
        turns = "".join(
            _turn_html(order[f["tc_id"]], f, manifest, library, app, media_map, loaded)
            for f in facts
        )
        out.append(
            '<section class="goal" aria-labelledby="goal-'
            + str(gi)
            + '"><div class="goalhead"><h3 id="goal-'
            + str(gi)
            + '" dir="auto">'
            + esc(group["goal"], 400)
            + "</h3>"
            + _result_pill(_goal_result(group["cases"]))
            + '<span class="goalmeta">'
            + esc(" · ".join(meta), 80)
            + '</span></div><ol class="film">'
            + film
            + "</ol>"
            + turns
            + "</section>"
        )
    return "".join(out)


def _issues_html(facts: list, kinds: dict) -> str:
    """Every case that failed, was blocked or crashed, or ``""``."""
    items = []
    for f in facts:
        word = _unify_verdict(f["verdict"])
        crash = (f.get("crash") or {}).get("kind")
        if word not in ("fail", "blocked") and not crash:
            continue
        anchor = ("turn-" if kinds.get(f["tc_id"]) == "turn" else "case-") + f["slug"]
        items.append(
            '<li class="issue">'
            + _result_pill(word if word in ("fail", "blocked") else "fail")
            + '<a href="#'
            + anchor
            + '">'
            + esc(f["tc_id"], 40)
            + '</a><span dir="auto">'
            + esc(f.get("finding") or f["reason"] or f["title"], 300)
            + (("; the app crashed (" + esc(crash, 40) + ")") if crash else "")
            + "</span></li>"
        )
    return ('<ul class="issues">' + "".join(items) + "</ul>") if items else ""


def _results_html(groups: list, scripted: list, tally_: dict, total: int) -> str:
    """The header's result lines, inside the totals the selfcheck compares."""
    ordered = [(name, int(tally_.get(name) or 0)) for name in TILES]
    ordered += [
        (name, int(c)) for name, c in sorted(tally_.items()) if name not in TILES
    ]
    attrs = "".join(
        " data-" + name.replace("_", "-") + '="' + str(int(count)) + '"'
        for name, count in ordered
    )
    lines = []
    for group in groups:
        lines.append(
            '<li class="result">'
            + _result_pill(_goal_result(group["cases"]))
            + '<span dir="auto">'
            + esc(group["goal"], 400)
            + "</span></li>"
        )
    if scripted:
        words: dict = {}
        for f in scripted:
            word = _unify_verdict(f["verdict"])
            words[word] = words.get(word, 0) + 1
        lines.append(
            '<li class="result"><span class="rcount">'
            + esc(_count(len(scripted), "scripted case"), 40)
            + "</span><span>"
            + esc(
                " · ".join(str(words[w]) + " " + w for w in RESULTS if words.get(w)),
                120,
            )
            + "</span></li>"
        )
    if not lines:
        lines.append(
            '<li class="result"><span>No case has been checkpointed yet.</span></li>'
        )
    return (
        '<div id="'
        + SUMMARY_ID
        + '" data-total="'
        + str(int(total))
        + '"'
        + attrs
        + '><ul class="results">'
        + "".join(lines)
        + "</ul>"
        + (_segbar(tally_) if scripted else "")
        + "</div>"
    )


def _build_label(manifest: dict) -> str:
    for key in ("app_version", "version_name", "build"):
        value = _text(manifest.get(key), 40)
        if value:
            return value
    return ""


def _duration(manifest: dict, cases: list) -> str:
    start = _epoch(manifest.get("created"))
    end = last_checkpoint(cases)
    if start is None or end is None or end < start:
        return ""
    return fmt_ms(int((end - start) * 1000))


def _meta_html(
    manifest: dict, cases: list, partial: bool, coverage: dict | None = None
) -> str:
    device = manifest.get("device") if isinstance(manifest.get("device"), dict) else {}
    model = _text(device.get("model"), 40)
    api = _text(device.get("api"), 8)
    kind = DEVICE_KINDS.get(str(device.get("kind") or ""), "")
    what = (
        " · ".join(
            bit
            for bit in (
                model or _text(manifest.get("avd"), 40),
                ("Android API " + api) if api else "",
                kind,
            )
            if bit
        )
        or _text(manifest.get("serial"), 40)
        or "device not recorded"
    )
    items = [("Device", what), ("Date", _short_stamp(manifest.get("created")))]
    took = _duration(manifest, cases)
    if took:
        items.append(("Duration", took))
    state = "finished"
    if partial:
        try:
            planned = int(manifest.get("total") or 0)
        except (TypeError, ValueError, OverflowError):
            planned = 0
        waiting = max(0, planned - len(cases))
        state = "in progress" + (
            (" — " + str(waiting) + " not yet checkpointed") if waiting else ""
        )
    items.append(("State", state))
    # The one coverage sentence (run_store owns it): "finished" alone would read
    # as "every case judged" on a run that judged two of three.
    if coverage:
        items.append(("Verdicts", coverage_phrase(coverage)))
    # ONE producer with `verdict_line`'s own tally (`mobile_render.typed_field_tally`)
    # -- the report and the chat reply must never disagree on a field count.
    tally = mobile_render.typed_field_tally(cases).strip()
    if tally:
        items.append(("Typed fields", tally))
    return "".join(
        "<span><b>" + esc(k, 20) + "</b> " + esc(v, 200) + "</span>"
        for k, v in items
        if v
    )


#: The screen-capture fields Diagnostics tabulates, in this order.
CAPTURE_FIELDS = (
    "dropped_inside_panel",
    "dropped_outside_panel",
    "dropped_unclassified",
    "considered",
    "panel_axes",
    "dialog_package",
)


def _capture_value(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return " × ".join(_text(v, 12) for v in value)
    return _text(value, 60)


def _capture_table(screens: object) -> str:
    """One row per stored screen; a column no screen fills is left out."""
    book = screens if isinstance(screens, dict) else {}
    rows = [
        (str(sid), {k: _capture_value(s.get(k)) for k in CAPTURE_FIELDS})
        for sid, s in sorted(book.items())
        if isinstance(s, dict)
    ]
    cols = [k for k in CAPTURE_FIELDS if any(r[k] not in ("", "0") for _sid, r in rows)]
    if not rows or not cols:
        return ""
    head = "<th>screen</th>" + "".join(
        "<th>" + esc(k.replace("_", " "), 40) + "</th>" for k in cols
    )
    body = "".join(
        "<tr><td>"
        + esc(sid, 40)
        + "</td>"
        + "".join("<td>" + esc(r[k], 60) + "</td>" for k in cols)
        + "</tr>"
        for sid, r in rows
    )
    return (
        '<div class="tablewrap"><table class="cov"><thead><tr>'
        + head
        + "</tr></thead><tbody>"
        + body
        + "</tbody></table></div>"
    )


def _diag_part(sid: str, title: str, note: str, body: str) -> str:
    """One collapsed Diagnostics part, or ``""`` when it has nothing to say."""
    if not body:
        return ""
    return (
        '<details class="diag" id="'
        + sid
        + '"><summary>'
        + esc(title, 60)
        + (('<span class="diagnote">' + esc(note, 120) + "</span>") if note else "")
        + '</summary><div class="diagbody">'
        + body
        + "</div></details>"
    )


def _app_logged(loaded: object) -> bool:
    """Whether the run holds an app log.

    When it does not, the Diagnostics "App evidence" part says why, once. Every
    case and turn repeating the same run-wide reason said nothing new.
    """
    content = loaded.get("content") if isinstance(loaded, dict) else None
    return isinstance(content, dict) and content.get("source") not in (None, "none")


def _has_network(facts: list, loaded: dict | None) -> bool:
    for f in facts:
        try:
            if int((f.get("network") or {}).get("packets") or 0) > 0:
                return True
        except (TypeError, ValueError, OverflowError):
            pass
        if (f.get("capture") or {}).get("flows"):
            return True
    # The loader answers {"error", "content"}; a content whose source is
    # "none" read nothing (no capture, the flag off, no profile).
    content = loaded.get("content") if isinstance(loaded, dict) else None
    return isinstance(content, dict) and content.get("source") not in (None, "", "none")


def _about_html() -> str:
    """How to read the page, said once."""
    rows = [
        (
            "Results",
            "Every goal, turn and case reads as one of four words. Pass, fail and "
            "blocked are what the model that drove the run concluded after its last "
            "action; unfinished means it has not concluded yet. The report replays and "
            "records; it never judges a screen itself.",
        ),
        (
            "Pictures",
            "Each step shows the device's own screenshot before and after it. The "
            "outlined box and dot mark the element the step acted on. Where no "
            "picture was stored the step says so in its place. Pictures live in the "
            "run folder beside this page (../shots and media/), so keep the folder "
            "together when you share it.",
        ),
        ("Recordings", CLIP_SCALE_NOTE),
        (
            "LLM turns",
            "The planning turns the tester\u2019s own chat model took: one "
            "plan per case, every escape-hatch re-plan, and every stop the server did "
            "not charge as an escape. Tokens and cost are not shown: no model call "
            "passes through this server, so it has nothing to meter. The figure can "
            "exceed the escape-hatch count beside it; the difference is the stops this "
            "run was given for free.",
        ),
        (
            "Private data",
            "Typed passwords and other marked values are written as *** in text. "
            "Screenshots and recordings cannot be masked: anything visible on the "
            "screen is in this folder, so treat it like the device itself.",
        ),
    ]
    rows += [(term, why) for term, why in PAGE_LIMITS]
    return (
        '<dl class="about">'
        + "".join(
            "<dt>" + esc(term, 120) + "</dt><dd>" + esc(why, 600) + "</dd>"
            for term, why in rows
        )
        + "</dl>"
    )


def _document_body(
    *,
    run_id: str,
    manifest: dict,
    cases: list,
    screens: object,
    lease: dict,
    tally: dict,
    observations: object = None,
    partial: bool,
    coverage: dict | None = None,
) -> str:
    # run_id is this branch's addition (the capture reader needs it to find the
    # per-case flows); media_map is main's. Both are required: dropping either
    # silently disables one lane's evidence.
    facts = [_case_facts(case, manifest, run_id) for case in cases]
    # The run's pixels, joined ONCE and handed to every section that shows one.
    media_map = _media_map(cases)
    # TWO libraries, joined once. ``screens`` answers which screens the run
    # visited and stays the accessibility audit's input -- one row per screen,
    # not one per look at it. ``library`` adds every stored OBSERVATION and is
    # what a FRAME resolves against, so a four-turn conversation draws four
    # pictures instead of one. Merged rather than passed as a pair because the
    # keys do not collide IN PRACTICE: `perception._screen_id` emits twelve hex
    # characters and an observation id is that id plus a dash and up to twelve
    # more, so no screen id has the shape of an observation key.
    #
    # NOT an invariant the code enforces, and the comment used to claim it was.
    # `run_store._RUN_ID_RE` accepts up to 64 characters of [A-Za-z0-9._-], so a
    # STORED screen id of "aabbccdd0011-000000000000" is admissible and would
    # collide, with the observation silently winning this `update`. Nothing
    # produces such an id today; if something ever does, the fix is two
    # dictionaries rather than a longer comment. Stated because an invariant
    # asserted in prose and unenforced in code is how a reader stops checking.
    library = _merged_library(screens, observations)
    # Defaulted rather than required, so a caller that has not computed it still
    # gets THE producer's answer and never a locally invented one.
    coverage = (
        coverage if isinstance(coverage, dict) else verdict_coverage(cases, manifest)
    )
    app = _app_label(manifest)
    # The app's side of the run (plan P3): read ONCE, joined to the cases, and
    # handed to every section. Never raises; a run without a capture, or a
    # package without a profile, renders every evidence fragment as a stated gap.
    loaded = ev_render.load_run_evidence(
        run_id, manifest, cases, ev_profiles.profile_for(manifest.get("package"))
    )
    # A run is goals, turns and steps. An exploratory case is a TURN, drawn in
    # the journey of the goal it drove at; every other case is a scripted case,
    # drawn in the case list. One map says which, so no case is drawn twice.
    #
    # `findings` is built by `_logs_section` and only there: building it twice
    # is how this page once emitted id="findings" twice -- see
    # test_no_id_is_emitted_twice_on_the_rendered_page.
    kinds = {
        _text(c.get("tc_id"), 40): (
            "turn" if _case_type(c, manifest) == EXPLORATORY else "case"
        )
        for c in cases
        if isinstance(c, dict)
    }
    facts_by_id = {f["tc_id"]: f for f in facts}
    cases_by_id = {_media_key(c.get("tc_id")): c for c in cases if isinstance(c, dict)}
    turns = {
        tc: facts_by_id[tc]
        for tc, kind in kinds.items()
        if kind == "turn" and tc in facts_by_id
    }
    scripted = [f for f in facts if kinds.get(f["tc_id"]) != "turn"]
    groups = _derive_goals(cases, manifest)
    issues = _issues_html(facts, kinds)
    journey = _journey_html(
        groups, turns, cases_by_id, manifest, library, app, media_map, loaded
    )
    cases_body = (
        _cases_html(scripted, library, app, loaded, coverage, observations, media_map)
        if scripted
        else ""
    )
    a11y = _a11y_section(screens)
    network = (
        ev_render.apis_section(loaded)
        + ev_render.network_section(cases)
        + ev_render.capture_section(cases, manifest)
        + _perf(facts, loaded)
        if _has_network(facts, loaded)
        else ""
    )
    # The app's own capture, summarised: its tiles, the session it came from,
    # and the trust checks -- including why nothing was read (the flag is off,
    # the load hit its byte cap). The old overview carried these.
    tiles = "".join(ev_render.overview_tiles(loaded)) if loaded else ""
    evidence = (
        (('<div class="kpis run">' + tiles + "</div>") if tiles else "")
        + (ev_render.session_block(loaded) + ev_render.trust_block(loaded))
        if loaded
        else ""
    )
    # Built after the journey and the case list: every image note those
    # pictures raised is in the book by now.
    notes = "".join(
        "<li>" + esc(note, 300) + "</li>"
        for note in list((_IMAGES.get() or {}).get("notes") or [])
    )
    diagnostics = (
        _diag_part(
            "screen-capture",
            "Screen capture",
            "what the element reader kept and dropped",
            _capture_table(library),
        )
        + _diag_part(
            "images",
            "Image processing",
            "",
            ('<ul class="plain">' + notes + "</ul>") if notes else "",
        )
        + _diag_part(
            "evidence", "App evidence", "what the app's own capture holds", evidence
        )
        + _diag_part("network", "Network", "the app's own calls", network)
        + _diag_part(
            "logs", "Logs and findings", "", _logs_section(manifest, facts, len(facts))
        )
        + _diag_part("diag-a11y", "Accessibility", "", a11y)
        + _diag_part(
            "env",
            "Environment",
            "what this run ran on",
            _facts_strip(run_id, manifest, lease, partial, loaded, coverage)
            + _env_section(manifest, loaded),
        )
    )
    parts = [
        (ISSUES_ID, "Issues", "what failed or was blocked", issues),
        (JOURNEY_ID, "Journey", "what the explorer did, turn by turn", journey),
        (CASES_ID, "Scripted cases", "every planned case and how it ended", cases_body),
        (DIAG_ID, "Diagnostics", "technical detail, collapsed", diagnostics),
        (ABOUT_ID, "About this report", "how to read it", _about_html()),
    ]
    # The nav is built from what was emitted and only from it, so it can never
    # offer a section this page does not have.
    present = [part for part in parts if part[3]]
    nav = "".join(
        '<a href="#' + sid + '" data-nav="' + sid + '">' + esc(title, 40) + "</a>"
        for sid, title, _label, _body in present
    )
    sections = (
        "".join(
            _sechead(sid, title, label, "", body) for sid, title, label, body in present
        )
        + '<div id="'
        + END_ID
        + '" data-cards="'
        + str(len(cases))
        + '"></div>'
    )
    build = _build_label(manifest)
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
                "DOC_TITLE": esc(app + " · mobile run " + str(run_id), 160),
                "BRAND": "Mobile run",
                "BRAND_SUB": "QA Agents",
                "NAV": nav,
                "EYEBROW": "Mobile run " + esc(run_id, 80),
                "TITLE": '<span dir="auto">'
                + esc(app, 80)
                + "</span>"
                + (
                    (' <span class="build">' + esc(build, 40) + "</span>")
                    if build
                    else ""
                ),
                "META": _meta_html(manifest, cases, partial, coverage),
                "RESULTS": _results_html(groups, scripted, tally, len(facts)),
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
    whole = "<style>" + SHELL_STYLE + "</style>"
    if whole in body:
        markup = body.replace(whole, "", 1)
        body = body.replace(whole, "<style>" + _page_style(markup) + "</style>", 1)
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


def explore_stop(manifest: object) -> str:
    """Why this exploratory run has stopped, or ``""``. ONE producer.

    ``explore_runner.display_stop`` is the callee, and the producer underneath it
    is ``stop_reason`` with THREE clauses: a recorded ``stop``, the turn budget,
    and the wall-clock deadline. The two names are one core with two contracts:
    ``display_stop`` is TOTAL because this is a RENDER path, while
    ``stop_reason`` stays strict because ``session.resolve``'s containment gate
    IS its raise. Reading
    ``explore["stop"]`` alone -- which this module used to do -- answers only the
    first, so a run whose deadline passed with no final turn read as still
    running forever. Measured on mrun-20260908-061316-a41b2a: the page said "in
    progress" 1h42m after the deadline, while ``session.resolve`` -- the other
    consumer of the same fact, at the anchor
    ``stop = explore_runner.stop_reason(explore)`` -- already said ``report``.
    Cited by ANCHOR and not by line: several sessions are moving that file, and
    source rots faster than a comment about it.

    ``""`` for a suite run: the question does not apply there, and the caller's
    own checkpoint arithmetic answers it.
    """
    # The RENDER contract, delegated rather than re-implemented. NOT
    # `stop_reason`: that one is strict on purpose -- it is `session.resolve`'s
    # containment gate -- and a manifest with a junk `deadline` reaching it from
    # here raised through `_is_partial` into `render`'s outer `except`, which
    # writes no page at all.
    return explore_runner.display_stop(manifest)


def _is_partial(manifest: dict, cases: list, tally: dict) -> bool:
    """Whether this run is still going, decided from the FILES.

    The manifest's ``cases`` key is never consulted -- a run planned by an older
    build may not have one, and the checkpoints are the authority anyway.
    """
    if str(manifest.get("lane") or "") == "explore":
        return not bool(explore_stop(manifest))
    done = sum(int(count) for name, count in tally.items() if name in DONE_VERDICTS)
    planned = 0
    try:
        planned = int(manifest.get("total") or 0)
    except (TypeError, ValueError, OverflowError):
        planned = 0
    return bool(not cases or done < max(planned, len(cases)))


def render(run_id: str) -> dict:
    """Write ``runs/<run_id>/report/index.html`` and its ``media/`` beside it.

    The artifact is a FOLDER: the page references its recordings and frames
    relatively, so the whole folder zips and opens anywhere. Built from that
    run's files only.

    ``{"error", "content": {"path", "partial", "cards", "totals", "bytes"}}``.
    Never raises.
    """
    try:
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
        # The evidence library, read beside the dedup library and never
        # instead of it: the audit wants one row per screen, a frame wants
        # the look the step actually took.
        observations = (run_store.list_observations(run_id) or {}).get("content") or {}
        lease = (run_store.read_lease(run_id) or {}).get("content") or {}
        lease = lease if isinstance(lease, dict) else {}
        # NOT `tally = tally(cases)`: that binds `tally` as a local for the
        # WHOLE function body, so the call on the right resolves to an unbound
        # local and EVERY invocation raises UnboundLocalError -- which this
        # function's own `except Exception` then reports as a handled error,
        # so the module looks alive and returns nothing. Found by EXECUTING.
        counts = tally(cases)
        partial = _is_partial(manifest, cases, counts)
        coverage = verdict_coverage(cases, manifest)
        page = _document(
            run_id=str(run_id),
            manifest=manifest,
            cases=cases,
            screens=screens,
            observations=observations,
            lease=lease,
            tally=counts,
            partial=partial,
            coverage=coverage,
        )
        target = report_path(str(run_id))
        written = _write_page(target, page)
        if written.get("error"):
            return written
        return {
            "error": None,
            "content": {
                "path": str(target),
                "partial": partial,
                "verdict_coverage": coverage,
                "cards": len(cases),
                "totals": counts,
                "bytes": len(page.encode("utf-8")),
            },
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.report.render failed")
        return {"error": str(exc), "content": None}
