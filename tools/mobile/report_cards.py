"""Case cards for the report: per-case facts, screens, planning, card HTML and the cases section."""

from __future__ import annotations

import json
import logging
from collections import Counter

from tools.mobile import run_store
from tools.mobile.report_frames import _look_pic, _seq_frames
from tools.mobile.report_media import _video_html
from tools.mobile.report_text import (
    LAT_BUCKETS,
    _lat_bucket,
    _slug,
    _stamp,
    _text,
    esc,
    fmt_ms,
)
from tools.mobile.report_trace import _AFTER_LOOK, _step_html, _trace_rows
from tools.mobile.report_verdict import (
    DONE_VERDICTS,
    TILES,
    _app_logged,
    _planned_case,
    _tone,
    _verdict_of,
    coverage_phrase,
)
from tools.mobile.report_widgets import (
    MSEP,
    RAIL,
    _all_chip,
    _chip,
    _filter_group,
    _metric,
    _PageData,
    _pill,
    _sec_block,
)
from tools.mobile_capture import flows as api_flows
from tools.mobile_evidence import crash_detector
from tools.mobile_evidence import exchanges as ev_exchanges
from tools.mobile_evidence import render as ev_render

# same logger as the old module; kept for log filters
logger = logging.getLogger("tools.mobile.report")


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
    facts = _case_record(safe, tc_id, run_id)
    facts.update(_case_planning(safe, rows))
    facts.update(_case_screens(safe, rows))
    facts.update(_case_planned(_planned_case(manifest, tc_id), rows))
    return facts


def _case_screens(safe: dict, rows: list) -> dict:
    """The screens a case started on, ended on and passed through."""
    first_before = rows[0]["before"] if rows else _text(safe.get("screen_id"), 40)
    last_after, last_obs, last_look = _last_looks(rows)
    last_substituted = bool(last_look) and last_look != last_obs
    # The observation each end WAS, kept beside the id each end IS. A card that
    # keys its frame on the id alone draws the last look at that screen, which
    # on a conversation is somebody else's turn.
    first_obs = rows[0]["before_obs"] if rows else ""
    screens_seen = {row["before"] for row in rows} | {row["after"] for row in rows}
    screens_seen.discard("")
    return {
        "first": first_before,
        "last": last_after,
        "first_obs": first_obs,
        "last_obs": last_obs,
        "last_look": last_look,
        "last_substituted": last_substituted,
        "screens": len(screens_seen),
    }


def _last_looks(rows: list) -> tuple:
    """``(last_after, last_obs, last_look)`` scanned from the end of *rows*."""
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
    # Substituted (``last_look != last_obs``) means an EARLIER look at the right
    # screen, so the caption must say so rather than claim it is the end state. A
    # record written before observations existed substitutes nothing and keeps
    # its old caption.
    return last_after, last_obs, last_look


def _case_record(safe: dict, tc_id: str, run_id: str) -> dict:
    """The identity, verdict and evidence a case checkpoint carries."""
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
    }


def _case_planning(safe: dict, rows: list) -> dict:
    """The case's escape, uncharged-stop and planning-turn counts."""
    try:
        escapes = int(safe.get("escapes") or 0)
    except (TypeError, ValueError, OverflowError):
        escapes = 0
    try:
        free_stops = max(0, int(safe.get("free_stops") or 0))
    except (TypeError, ValueError, OverflowError):
        free_stops = 0
    return {
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
    }


def _case_planned(planned: dict, rows: list) -> dict:
    """What the suite planned for the case, and how long it took."""
    wall = _case_wall(rows)
    return {
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


def _card_attrs(facts: dict, loaded: dict | None) -> str:
    """The card's ``data-*`` attributes: what the report toolbar filters and searches on.

    Their order is the page's: ``loaded`` evidence may override a key in place or
    add one at the end, and an empty value is dropped rather than rendered.
    """
    verdict = facts["verdict"]
    rows = facts["rows"]
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
    return " ".join(
        name + '="' + esc(value, 4000) + '"'
        for name, value in data.items()
        if value not in (None, "")
    )


def _card_tech(facts: dict, screens: object, loaded: dict | None) -> str:
    """The body of the card's folded "Technical detail" block."""
    rows = facts["rows"]
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
    return (
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
    snippet = _card_snippet(first_action, facts["reason"])
    bits = _card_bits(facts, pill_cls, label, loaded)
    attrs = _card_attrs(facts, loaded)
    expected = (
        esc(facts["expected"], 600)
        if facts["expected"]
        else "the suite states no expected result for this case"
    )
    head = _card_head(facts, (pill_cls, label), snippet, attrs, "".join(bits))
    return (
        head
        + _card_body(
            facts,
            expected,
            _card_steps(facts, screens, app),
            _card_tech(facts, screens, loaded),
            media_map,
        )
        + "</div>\n</details>"
    )


def _card_snippet(first_action: str, reason: str) -> str:
    """The summary line: the first action, an arrow, the stated reason."""
    if not (first_action or reason):
        return ""
    return (
        '<span class="csnip">'
        + (('<q dir="auto">' + esc(first_action, 64) + "</q>") if first_action else "")
        + (
            '<span class="arr" aria-hidden="true">→</span>'
            if first_action and reason
            else ""
        )
        + (('<q class="sara" dir="auto">' + esc(reason, 64) + "</q>") if reason else "")
        + "</span>"
    )


def _card_bits(facts: dict, pill_cls: str, label: str, loaded: dict | None) -> list:
    """The summary metrics: verdict pill, step, wall, turn and screen counts."""
    bits = [_pill(pill_cls, label), MSEP, _metric(len(facts["rows"]), "steps")]
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
    return bits


def _card_steps(facts: dict, screens: object, app: str) -> str:
    """The case's step list, or the honest note that none was replayed."""
    rows = facts["rows"]
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
    if not rows:
        pic = (
            _look_pic(first_key, _PageData(screens, app), "On screen")
            if first_key
            else ""
        )
        return (
            '<p class="empty-note big">No step has been replayed for this case yet.</p>'
            + (('<div class="steppics">' + pic + "</div>") if first_key else "")
        )
    return (
        '<ol class="steplist">'
        + "".join(
            _step_html(
                facts["slug"],
                i,
                {**row, _AFTER_LOOK: last} if last and i == len(rows) - 1 else row,
                screens,
                app,
            )
            for i, row in enumerate(rows)
        )
        + "</ol>"
    )


def _card_head(facts: dict, tone: tuple, snippet: str, attrs: str, bits: str) -> str:
    """The card's opening tag and its summary row."""
    pill_cls, label = tone
    return (
        '\n<details class="case rail-'
        + RAIL.get(facts["verdict"], "none")
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
        + bits
        + "</div>\n  </summary>"
    )


def _card_body(
    facts: dict, expected: str, steps: str, tech: str, media_map: object
) -> str:
    """The card's body: expected result, strips, steps, end state, tech fold."""
    return (
        '\n  <div class="cbody"><p class="cexp"><b>Expected</b> — '
        + expected
        + "</p>"
        + _vstrip(facts)
        + _video_html(facts["tc_id"], media_map)
        + steps
        + _ended_block(facts)
        + '<details class="tech"><summary>Technical detail</summary>'
        + tech
        + "</details>"
    )


def _toolbar_groups(facts: list) -> list:
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
    return groups


def _toolbar_sorts(loaded: dict | None) -> str:
    sorts = (
        ("order", "case order"),
        ("steps", "actions, most first"),
        ("wall", "replay time, longest first"),
        ("plans", "LLM turns, most first"),
        ("escapes", "escape-hatch turns, most first"),
    ) + (tuple(ev_render.extra_sorts(loaded)) if loaded else ())
    return "".join(
        '<option value="' + k + '">' + label + "</option>" for k, label in sorts
    )


def _toolbar(facts: list, loaded: dict | None = None) -> str:
    groups = _toolbar_groups(facts)
    if loaded:
        groups += ev_render.toolbar_groups(loaded, facts)
    opts = _toolbar_sorts(loaded)
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


def _cases_section(facts: list, page: _PageData) -> str:
    loaded, coverage = page.loaded, page.coverage
    cards = "".join(
        _card_html(f, page.library, page.app, loaded, page.media_map) for f in facts
    )
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


def _cases_html(facts: list, page: _PageData, observations: object) -> str:
    """The cases section, plus the store's own disclosure when the run reached
    its observation limit.

    Derived HERE and nowhere else. A truncated evidence set that renders as a
    complete one is the failure this whole change exists to end, so the page
    says the limit was reached rather than letting a frame read as "not
    captured" for a reason nobody can see.
    """
    body = _cases_section(facts, page)
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
