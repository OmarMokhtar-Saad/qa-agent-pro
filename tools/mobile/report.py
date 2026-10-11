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
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tools.mobile import actions as actions_mod  # noqa: F401
from tools.mobile import render as mobile_render
from tools.mobile import (
    run_store,
)
from tools.mobile.report_cards import (
    _capture_of,  # noqa: F401
    _card_attrs,  # noqa: F401
    _card_bits,  # noqa: F401
    _card_body,  # noqa: F401
    _card_head,  # noqa: F401
    _card_html,  # noqa: F401
    _card_snippet,  # noqa: F401
    _card_steps,  # noqa: F401
    _card_tech,  # noqa: F401
    _case_card,  # noqa: F401
    _case_facts,
    _case_planned,  # noqa: F401
    _case_planning,  # noqa: F401
    _case_record,  # noqa: F401
    _case_screens,  # noqa: F401
    _case_wall,  # noqa: F401
    _cases_html,
    _cases_section,  # noqa: F401
    _crash_block,  # noqa: F401
    _ended_block,  # noqa: F401
    _expected_of,  # noqa: F401
    _last_looks,  # noqa: F401
    _network_of,  # noqa: F401
    _toolbar,  # noqa: F401
    _toolbar_groups,  # noqa: F401
    _toolbar_sorts,  # noqa: F401
    _vstrip,  # noqa: F401
)
from tools.mobile.report_diagnostics import (
    CAPTURE_FIELDS,  # noqa: F401
    DEVICE_KIND_UNKNOWN,  # noqa: F401
    DEVICE_KINDS,
    PAGE_LIMITS,  # noqa: F401
    SYSTEM_IMAGE_ABSENT,  # noqa: F401
    _a11y_lede,  # noqa: F401
    _a11y_rows,  # noqa: F401
    _a11y_screen_row,  # noqa: F401
    _a11y_section,  # noqa: F401
    _about_html,
    _app_label,
    _capture_table,  # noqa: F401
    _capture_value,  # noqa: F401
    _device_sub,  # noqa: F401
    _diag_head,  # noqa: F401
    _diag_part,  # noqa: F401
    _diag_tail,  # noqa: F401
    _diagnostics_html,
    _env_host_row,  # noqa: F401
    _env_row,  # noqa: F401
    _env_section,  # noqa: F401
    _evidence_html,  # noqa: F401
    _facts_strip,  # noqa: F401
    _findings_lede,  # noqa: F401
    _findings_section,  # noqa: F401
    _findings_table,  # noqa: F401
    _has_network,  # noqa: F401
    _lease_cells,  # noqa: F401
    _locale_cell,  # noqa: F401
    _logs_section,  # noqa: F401
    _network_html,  # noqa: F401
    _perf,  # noqa: F401
    _perf_figure,  # noqa: F401
    _perf_legend,  # noqa: F401
    _perf_run_rows,  # noqa: F401
    _perf_tile,  # noqa: F401
    _perf_tiles,  # noqa: F401
    _replayed_turns,  # noqa: F401
    _run_cells,  # noqa: F401
    _spoken_turns,  # noqa: F401
    _timings_part,  # noqa: F401
)
from tools.mobile.report_frames import (
    _SHOTS,
    _WIRE_LEGEND,  # noqa: F401
    AMBIGUOUS_LOOK,  # noqa: F401
    LOOK_AMBIGUOUS,  # noqa: F401
    LOOK_EXACT,  # noqa: F401
    LOOK_UNCAPTURED,  # noqa: F401
    MAX_DEVICE_EXTENT,  # noqa: F401
    MAX_FRAME_H,  # noqa: F401
    MAX_FRAME_W,  # noqa: F401
    MAX_RECTS,  # noqa: F401
    NOT_CAPTURED,  # noqa: F401
    SHOT_MISSING_NOTE,  # noqa: F401
    SHOT_SRC,  # noqa: F401
    STEP_SIZES,  # noqa: F401
    TARGET_OPS,  # noqa: F401
    _bounds_of,  # noqa: F401
    _device_size,  # noqa: F401
    _drawn_frame,  # noqa: F401
    _element_map_fold,  # noqa: F401
    _elements_extent,  # noqa: F401
    _empty_phone,  # noqa: F401
    _exact_pic_body,  # noqa: F401
    _frame_box,  # noqa: F401
    _frame_html,  # noqa: F401
    _frame_scale,  # noqa: F401
    _geometry_note,  # noqa: F401
    _look_is_ambiguous,  # noqa: F401
    _look_pic,  # noqa: F401
    _missing_frame,  # noqa: F401
    _phone_html,  # noqa: F401
    _place_elements,  # noqa: F401
    _rect_html,  # noqa: F401
    _rect_of,  # noqa: F401
    _resolve_look,  # noqa: F401
    _same_gap,  # noqa: F401
    _seq_frames,  # noqa: F401
    _shot_html,  # noqa: F401
    _shots,
    _step_pics,  # noqa: F401
    _stored_extent,  # noqa: F401
    _tap_overlay,  # noqa: F401
    scale_bounds,  # noqa: F401
    wireframe,  # noqa: F401
)
from tools.mobile.report_journey import (
    RESULT_LABEL,  # noqa: F401
    RESULT_PILL,  # noqa: F401
    RESULT_TONE,  # noqa: F401
    RESULTS,  # noqa: F401
    SUMMARY_ID,  # noqa: F401
    _count,  # noqa: F401
    _finding_of,  # noqa: F401
    _goal_meta,  # noqa: F401
    _issues_html,
    _journey_html,
    _result_pill,  # noqa: F401
    _results_html,
    _thumb_html,  # noqa: F401
    _turn_clips,  # noqa: F401
    _turn_extra,  # noqa: F401
    _turn_html,  # noqa: F401
    _turn_meta,  # noqa: F401
    _turn_tech_rows,  # noqa: F401
)
from tools.mobile.report_media import (
    _IMAGES,
    _MEDIA_NAME,  # noqa: F401
    CLIP_SCALE_NOTE,  # noqa: F401
    THUMB_SIZES,  # noqa: F401
    _clip_html,  # noqa: F401
    _image_book,
    _image_note,  # noqa: F401
    _image_notes_html,  # noqa: F401
    _img_html,  # noqa: F401
    _media_key,
    _media_map,
    _media_note,  # noqa: F401
    _media_src,  # noqa: F401
    _turn_frame,  # noqa: F401
    _variants,  # noqa: F401
    _video_html,  # noqa: F401
)
from tools.mobile.report_shell import (
    _CLASS_ATTR,  # noqa: F401
    _CSS_CLASS,  # noqa: F401
    _CSS_NOT,  # noqa: F401
    _SCRIPT_CLASSES,  # noqa: F401
    _SLOT,  # noqa: F401
    INDEX_FILE,  # noqa: F401
    MEDIA_DIR,  # noqa: F401
    REPORT_DIR,  # noqa: F401
    SHELL,
    SHELL_PATH,  # noqa: F401
    SHELL_SCRIPT,  # noqa: F401
    SHELL_STYLE,
    _between,  # noqa: F401
    _block_end,  # noqa: F401
    _compact_css,  # noqa: F401
    _compact_js,  # noqa: F401
    _css_rules,  # noqa: F401
    _fill,
    _page_style,
    _read_shell,  # noqa: F401
    report_path,
)
from tools.mobile.report_text import (
    _CREDENTIAL_TERMS,  # noqa: F401
    _PROSE_CREDENTIAL,  # noqa: F401
    _PROSE_CREDENTIAL_SET,  # noqa: F401
    _SAFE_TOKEN,  # noqa: F401
    _SECRET_MIN,  # noqa: F401
    _SECRETS,
    _SLUG,  # noqa: F401
    LAST_CHECKPOINT_LABEL,  # noqa: F401
    LAST_CHECKPOINT_SUB,  # noqa: F401
    LAT_BUCKETS,  # noqa: F401
    MAX_TEXT,  # noqa: F401
    _epoch,
    _lat_bucket,  # noqa: F401
    _mask_prose,  # noqa: F401
    _ms,  # noqa: F401
    _neutralize_markers,  # noqa: F401
    _percentile,  # noqa: F401
    _run_secrets,
    _short_stamp,
    _slug,  # noqa: F401
    _stamp,  # noqa: F401
    _text,
    _token,  # noqa: F401
    elapsed_note,  # noqa: F401
    esc,
    fmt_ms,
    last_checkpoint,
)
from tools.mobile.report_trace import (
    _AFTER_LOOK,  # noqa: F401
    _FIXED_WORDS,  # noqa: F401
    _TARGET_VERBS,  # noqa: F401
    _TURN_OPS,  # noqa: F401
    _WAIT_OPS,  # noqa: F401
    MAX_ROWS,  # noqa: F401
    OP_KIND,  # noqa: F401
    OUTCOME_WORDS,  # noqa: F401
    _action_hint,  # noqa: F401
    _action_line,  # noqa: F401
    _changed,  # noqa: F401
    _element_of,  # noqa: F401
    _named_target,  # noqa: F401
    _notes_applied,  # noqa: F401
    _obs_key,  # noqa: F401
    _parts_html,  # noqa: F401
    _plain_action,  # noqa: F401
    _plain_turn_op,  # noqa: F401
    _plain_wait,  # noqa: F401
    _q,  # noqa: F401
    _rid_tail,  # noqa: F401
    _seq_bar,  # noqa: F401
    _seq_detail,  # noqa: F401
    _seq_head,  # noqa: F401
    _seq_item,  # noqa: F401
    _seq_rows,  # noqa: F401
    _step_html,  # noqa: F401
    _step_words,  # noqa: F401
    _trace_row,  # noqa: F401
    _trace_rows,  # noqa: F401
    _typed_literal,  # noqa: F401
)
from tools.mobile.report_verdict import (
    _OTHER_TONE,  # noqa: F401
    DONE_VERDICTS,
    EXPLORATORY,
    EXPLORE_GOAL_PREFIX,  # noqa: F401
    TILES,  # noqa: F401
    VERDICT_TONE,  # noqa: F401
    _app_logged,  # noqa: F401
    _case_type,
    _derive_goals,
    _goal_of,  # noqa: F401
    _goal_result,  # noqa: F401
    _planned_case,  # noqa: F401
    _run_goals,  # noqa: F401
    _run_kind,  # noqa: F401
    _tone,  # noqa: F401
    _unify_verdict,  # noqa: F401
    _verdict_of,  # noqa: F401
    coverage_phrase,
    error_classes,  # noqa: F401
    error_phrase,  # noqa: F401
    explore_stop,
    tally,
    verdict_coverage,
)
from tools.mobile.report_widgets import (
    MSEP,  # noqa: F401
    RAIL,  # noqa: F401
    _all_chip,  # noqa: F401
    _chip,  # noqa: F401
    _filter_group,  # noqa: F401
    _kpi,  # noqa: F401
    _metric,  # noqa: F401
    _outcome_pill,  # noqa: F401
    _PageData,
    _pill,  # noqa: F401
    _Run,
    _sec_block,  # noqa: F401
    _sechead,
    _segbar,  # noqa: F401
    _tile_order,  # noqa: F401
)
from tools.mobile_evidence import exchanges as ev_exchanges  # noqa: F401
from tools.mobile_evidence import profiles as ev_profiles
from tools.mobile_evidence import render as ev_render

logger = logging.getLogger(__name__)


#: The page-size pin, shared with ``report_selfcheck`` so the two cannot drift.
#:
#: The index is ONE file inside a self-contained folder: the pictures live in
#: ``../shots/`` and the recordings in ``report/media/``, so nothing on this page
#: is an inlined payload any more. What this bounds is the MARKUP -- a run with
#: thousands of steps grows its case tables without any limit of their own, and a
#: document a browser cannot open is a report the tester cannot read at all.
#: That, and not the pictures, is the surviving constraint.
MAX_PAGE_BYTES = 8 * 1024 * 1024


END_ID = "qa-report-end"


@dataclass(frozen=True, kw_only=True)
class _DocInput:
    """The keyword arguments of one page build, carried as a single value."""

    run_id: str
    manifest: dict
    cases: list
    screens: object
    lease: dict
    tally: dict
    partial: bool
    observations: object = None
    coverage: dict | None = None


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
        return _document_body(_DocInput(**kwargs))
    finally:
        _SECRETS.reset(secrets)
        _IMAGES.reset(images)
        _SHOTS.reset(token)


#: Every environment fact this page can state, and the named reason for each it cannot.
#: One row per fact, and a row is NEVER omitted when the value is missing -- an omitted
#: row reads as "nothing to say", which is the success shape.


#: The run's last known moment, DERIVED, and labelled as derived everywhere it is shown.
#:
#: The manifest carries ``created`` and NO ``ended``: nothing writes one, because a run can
#: be abandoned and an abandoned run has no honest end. So this page never prints the word
#: "ended". What it can say is when the newest case was last checkpointed, which is a
#: different fact with a different name -- and one producer for it, so the masthead, the
#: overview strip and the timeline cannot disagree about when the run stopped.


# ── the run page: header, issues, journey, scripted cases, diagnostics ─────────


#: Section ids, top to bottom. The nav lists only the ones this run fills.
ISSUES_ID = "issues"
JOURNEY_ID = "journey"
CASES_ID = "cases"
DIAG_ID = "diagnostics"
ABOUT_ID = "about"
KNOWLEDGE_ID = "knowledge"


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


def _coverage_of(coverage: object, cases: list, manifest: dict) -> dict:
    """Defaulted rather than required, so a caller that has not computed it still
    gets THE producer's answer and never a locally invented one.
    """
    return coverage if isinstance(coverage, dict) else verdict_coverage(cases, manifest)


def _load_evidence(run_id: str, manifest: dict, cases: list) -> dict | None:
    """The app's side of the run (plan P3): read ONCE, joined to the cases, and
    handed to every section. Never raises; a run without a capture, or a
    package without a profile, renders every evidence fragment as a stated gap.
    """
    return ev_render.load_run_evidence(
        run_id, manifest, cases, ev_profiles.profile_for(manifest.get("package"))
    )


def _split_cases(run: _Run) -> tuple:
    """``(kinds, turns, scripted)``: the turn facts by ``tc_id`` and the rest."""
    kinds = _case_kinds(run.cases, run.manifest)
    facts_by_id = {f["tc_id"]: f for f in run.facts}
    turns = {
        tc: facts_by_id[tc]
        for tc, kind in kinds.items()
        if kind == "turn" and tc in facts_by_id
    }
    scripted = [f for f in run.facts if kinds.get(f["tc_id"]) != "turn"]
    return kinds, turns, scripted


def _case_kinds(cases: list, manifest: dict) -> dict:
    """``tc_id`` -> ``"turn"`` or ``"case"``.

    A run is goals, turns and steps. An exploratory case is a TURN, drawn in
    the journey of the goal it drove at; every other case is a scripted case,
    drawn in the case list. One map says which, so no case is drawn twice.

    `findings` is built by `_logs_section` and only there: building it twice
    is how this page once emitted id="findings" twice -- see
    test_no_id_is_emitted_twice_on_the_rendered_page.
    """
    return {
        _text(c.get("tc_id"), 40): (
            "turn" if _case_type(c, manifest) == EXPLORATORY else "case"
        )
        for c in cases
        if isinstance(c, dict)
    }


def _nav_and_sections(present: list, run_id: str, n_cases: int) -> tuple:
    """The nav links and the section markup of the parts that were emitted."""
    nav = "".join(
        '<a href="#' + sid + '" data-nav="' + sid + '">' + esc(title, 40) + "</a>"
        for sid, title, _label, _body in present
    )
    sections = (
        "".join(
            _sechead(sid, title, label, "", body) for sid, title, label, body in present
        )
        + _knowledge_html(run_id)
        + '<div id="'
        + END_ID
        + '" data-cards="'
        + str(n_cases)
        + '"></div>'
    )
    return nav, sections


def _source_digest(run_id: str, manifest: dict, n_cases: int) -> str:
    """The short stamp that ties a page to the run it was built from."""
    return hashlib.sha1(
        (
            str(run_id) + ":" + str(manifest.get("created") or "") + ":" + str(n_cases)
        ).encode("utf-8")
    ).hexdigest()[:12]


def _title_html(app: str, build: str) -> str:
    """The page title: the app name and, when known, its build."""
    return (
        '<span dir="auto">'
        + esc(app, 80)
        + "</span>"
        + ((' <span class="build">' + esc(build, 40) + "</span>") if build else "")
    )


def _fill_page(make_slots: Callable[[], dict]) -> str:
    """Fill the shell, prune its stylesheet to what the page uses, and wrap it.

    The slots are built INSIDE the guard, so a slot that raises still releases.
    """
    try:
        body = _fill(SHELL, make_slots())
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


def _present_parts(
    issues: str, journey: str, cases_body: str, diagnostics: str
) -> list:
    """The top-level parts that were emitted. The nav is built from these and
    only from these, so it can never offer a section this page does not have.
    """
    parts = [
        (ISSUES_ID, "Issues", "what failed or was blocked", issues),
        (JOURNEY_ID, "Journey", "what the explorer did, turn by turn", journey),
        (CASES_ID, "Scripted cases", "every planned case and how it ended", cases_body),
        (DIAG_ID, "Diagnostics", "technical detail, collapsed", diagnostics),
        (ABOUT_ID, "About this report", "how to read it", _about_html()),
    ]
    return [part for part in parts if part[3]]


def _page_slots(run: _Run, app: str, present: list, partial: bool) -> dict:
    """The shell slots but ``RESULTS``, which the caller owns."""
    nav, sections = _nav_and_sections(present, run.run_id, len(run.cases))
    build = _build_label(run.manifest)
    digest = _source_digest(run.run_id, run.manifest, len(run.cases))
    return {
        "DOC_TITLE": esc(app + " · mobile run " + str(run.run_id), 160),
        "BRAND": "Mobile run",
        "BRAND_SUB": "QA Agents",
        "NAV": nav,
        "EYEBROW": "Mobile run " + esc(run.run_id, 80),
        "TITLE": _title_html(app, build),
        "META": _meta_html(run.manifest, run.cases, partial, run.coverage),
        "SECTIONS": sections,
        "FOOTER_META": _footer_html(run.run_id, run.manifest),
        "SOURCE_STAMP": esc(str(run.run_id) + ":" + digest, 80),
        # Never used by this lane: nothing here is big enough to park.
        "STORES": "",
    }


# `_document_body` joins TWO libraries, once. ``screens`` answers which screens
# the run visited and stays the accessibility audit's input -- one row per
# screen, not one per look at it. ``library`` adds every stored OBSERVATION and
# is what a FRAME resolves against, so a four-turn conversation draws four
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
#
# run_id is this branch's addition (the capture reader needs it to find the
# per-case flows); media_map is main's. Both are required: dropping either
# silently disables one lane's evidence.
def _document_body(doc: _DocInput) -> str:
    run_id, manifest, cases = doc.run_id, doc.manifest, doc.cases
    screens, observations, lease = doc.screens, doc.observations, doc.lease
    tally, partial, coverage = doc.tally, doc.partial, doc.coverage
    facts = [_case_facts(case, manifest, run_id) for case in cases]
    media_map = _media_map(cases)
    library = _merged_library(screens, observations)
    coverage = _coverage_of(coverage, cases, manifest)
    app = _app_label(manifest)
    loaded = _load_evidence(run_id, manifest, cases)
    run = _Run(run_id, manifest, cases, facts, library, loaded, coverage)
    kinds, turns, scripted = _split_cases(run)
    groups = _derive_goals(cases, manifest)
    issues = _issues_html(facts, kinds)
    cases_by_id = {_media_key(c.get("tc_id")): c for c in cases if isinstance(c, dict)}
    page = _PageData(library, app, loaded, coverage, media_map)
    journey = _journey_html(groups, turns, cases_by_id, manifest, page)
    cases_body = _cases_html(scripted, page, observations) if scripted else ""
    diagnostics = _diagnostics_html(run, screens, lease, partial)
    present = _present_parts(issues, journey, cases_body, diagnostics)
    return _fill_page(
        lambda: {
            **_page_slots(run, app, present, partial),
            "RESULTS": _results_html(groups, scripted, tally, len(facts)),
        }
    )


def _knowledge_html(run_id: str) -> str:
    """The per-app knowledge section, ``""`` when nothing was learned. Never raises."""
    try:
        from tools.mobile import knowledge_report

        body = knowledge_report.render_section(
            knowledge_report.knowledge_section(run_id)
        )
        if not body:
            return ""
        return _sechead(
            KNOWLEDGE_ID, "App knowledge", "what this run learned", "", body
        )
    except Exception:
        logger.exception("knowledge section failed")
        return ""


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
        return not bool(explore_stop(manifest))
    done = sum(int(count) for name, count in tally.items() if name in DONE_VERDICTS)
    planned = 0
    try:
        planned = int(manifest.get("total") or 0)
    except (TypeError, ValueError, OverflowError):
        planned = 0
    return bool(not cases or done < max(planned, len(cases)))


def _render_run(run_id: str, manifest: dict) -> dict:
    """Read the run's files, build the page, write it; the ``render`` result."""
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
        return _render_run(run_id, manifest)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.report.render failed")
        return {"error": str(exc), "content": None}
