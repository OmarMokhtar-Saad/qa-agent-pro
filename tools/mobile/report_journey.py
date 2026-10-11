"""Journey and results rendering for the report: per-turn rows, goals, issues and the results section."""

from __future__ import annotations

from tools.mobile import run_store
from tools.mobile.report_cards import _crash_block
from tools.mobile.report_frames import _SHOTS, LOOK_EXACT, _resolve_look
from tools.mobile.report_media import (
    _IMAGES,
    THUMB_SIZES,
    _clip_html,
    _img_html,
    _media_key,
    _turn_frame,
)
from tools.mobile.report_shell import MEDIA_DIR
from tools.mobile.report_text import _text, esc, fmt_ms
from tools.mobile.report_trace import _step_html
from tools.mobile.report_verdict import (
    DONE_VERDICTS,
    _app_logged,
    _goal_result,
    _unify_verdict,
)
from tools.mobile.report_widgets import _PageData, _pill, _segbar, _tile_order
from tools.mobile_evidence import render as ev_render

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


def _turn_tech_rows(f: dict) -> str:
    """The ``dt``/``dd`` pairs of a turn's technical detail; empty values drop."""
    tech = [
        ("Case", f["tc_id"]),
        ("Status", f["status"] or "(none)"),
        ("Why it ended", f["reason"]),
    ]
    return "".join(
        "<dt>" + esc(k, 40) + '</dt><dd dir="auto">' + esc(v, 400) + "</dd>"
        for k, v in tech
        if v
    )


def _turn_extra(f: dict, loaded: dict | None) -> str:
    """Crash, log, network and capture blocks that close a turn's detail."""
    return (
        _crash_block(f.get("crash"))
        + (ev_render.turns_table(loaded, f["tc_id"]) if _app_logged(loaded) else "")
        + ev_render.case_network(f.get("network"))
        + ev_render.case_capture(f.get("capture"))
    )


def _turn_clips(f: dict, media_map: object) -> str:
    """The recordings stored for a turn's case."""
    return "".join(
        _clip_html(rec)
        for rec in ((media_map or {}).get("clips") or {}).get(_media_key(f["tc_id"]))
        or []
    )


def _turn_meta(f: dict) -> str:
    """``"3 steps · 1.2 s"``: the step count and, when known, the wall time."""
    meta = [_count(len(f["rows"]), "step")]
    if f["wall"] is not None:
        meta.append(fmt_ms(f["wall"]))
    return " · ".join(meta)


def _turn_html(n: int, f: dict, manifest: dict, page: _PageData) -> str:
    verdict = f["verdict"]
    pill = _result_pill(_unify_verdict(verdict)) if verdict in DONE_VERDICTS else ""
    steps = "".join(
        _step_html(f["slug"], i, row, page.library, page.app)
        for i, row in enumerate(f["rows"])
    )
    clips = _turn_clips(f, page.media_map)
    meta = _turn_meta(f)
    tech_rows = _turn_tech_rows(f)
    extra = _turn_extra(f, page.loaded)
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
        + esc(meta, 60)
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


def _goal_meta(facts: list) -> list:
    """The turn count, step count and summed wall time shown on a goal's head."""
    steps = sum(len(f["rows"]) for f in facts)
    walls = [f["wall"] for f in facts if f["wall"] is not None]
    meta = [_count(len(facts), "turn"), _count(steps, "step")]
    if walls:
        meta.append(fmt_ms(sum(walls)))
    return meta


def _journey_html(
    groups: list,
    facts_by_id: dict,
    cases_by_id: dict,
    manifest: dict,
    page: _PageData,
) -> str:
    library = page.library
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
        meta = _goal_meta(facts)
        film = "".join(
            _thumb_html(
                order[f["tc_id"]], f, _turn_frame(f["tc_id"], cases_by_id), library
            )
            for f in facts
        )
        turns = "".join(_turn_html(order[f["tc_id"]], f, manifest, page) for f in facts)
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
    attrs = "".join(
        " data-" + name.replace("_", "-") + '="' + str(int(count)) + '"'
        for name, count in _tile_order(tally_)
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
