"""HTML fragments for the app-evidence half of the mobile run report (plan P3).

``tools/mobile/report.py`` owns the page: the shell, the slots, the lane's own
rows. This module owns what the APP did underneath -- model round-trips, endpoint
calls, tool calls, flow cards -- as fragments the page drops into the sections it
already draws. Every function takes the ONE loaded bundle :func:`load_run_evidence`
returns and gives back markup; none reads the device or a store on its own.

Three invariants, the same three the page keeps:

1. **Every value reaches markup through :func:`ev_esc`** -- scrub (the learned
   header values, the sensitive-key pair net), neutralise the untrusted-content
   guard markers, then ``html.escape(quote=True)``. The exchange rows from
   ``exchanges`` escape through ``scrub.e``; the marker step is applied to their
   output here, so a hostile prompt cannot smuggle a guard sentinel onto the page.
2. **The learned-value net is armed before anything renders and released after.**
   :func:`load_run_evidence` calls ``scrub.learn_sensitive`` on the parsed report
   (plan D4); ``report.py`` calls :func:`release` once the page is built.
3. **A gap is stated, never blank.** A run with no capture, a package with no
   profile, a case whose window held no turn -- each renders the reason in the
   shell's ``empty-note`` markup. Nothing here raises to the page.
"""

from __future__ import annotations

import html
import logging
from collections import Counter

from config.settings import settings
from tools.mobile import run_store
from tools.mobile_evidence import evidence, exchanges, model, scrub

logger = logging.getLogger(__name__)

EVENTS_FILE = "events.ndjson"
MAX_TEXT = 200
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


# ── escaping ───────────────────────────────────────────────────────────────────


def _neutralize(text: str) -> str:
    from tools.mobile import perception

    out = text
    for marker in perception.GUARD_MARKERS:
        if marker and marker in out:
            out = out.replace(marker, perception.NEUTRALIZED)
    return out


def _flat(value: object, limit: int = MAX_TEXT) -> str:
    raw = "" if value is None else str(value)
    kept = "".join(
        " " if c in "\t\r\n" else c for c in raw if c == " " or c.isprintable()
    )
    kept = " ".join(kept.split())
    if len(kept) > limit:
        kept = kept[:limit] + "..."
    return kept


def ev_esc(value: object, limit: int = MAX_TEXT) -> str:
    """scrub -> neutralise guard markers -> escape. The ONE way a value reaches markup."""
    return html.escape(_neutralize(scrub.scrub_text(_flat(value, limit))), quote=True)


def _guard(markup: str) -> str:
    """The marker step for markup ``exchanges`` already escaped through ``scrub.e``."""
    return _neutralize(markup)


def _clip(text: object, n: int = CLIP) -> str:
    flat = _flat(text, 4000)
    return flat if len(flat) <= n else flat[: n - 1].rstrip() + "…"


# ── the shell's vocabulary (mirrors report.py's private helpers, by necessity) ──


def _pill(cls: str, label: object) -> str:
    return '<span class="pill ' + ev_esc(cls, 24) + '">' + ev_esc(label, 60) + "</span>"


def _metric(value: object, label: str, cls: str = "") -> str:
    return (
        '<span class="m '
        + ev_esc(cls, 24)
        + '"><b>'
        + ev_esc(value, 40)
        + "</b><i>"
        + ev_esc(label, 40)
        + "</i></span>"
    )


def _metric_pair(v1: object, l1: str, v2: object, l2: str, cls: str = "") -> str:
    return (
        '<span class="m '
        + ev_esc(cls, 24)
        + '"><b>'
        + ev_esc(v1, 40)
        + "</b><i>"
        + ev_esc(l1, 40)
        + "</i><b>"
        + ev_esc(v2, 40)
        + "</b><i>"
        + ev_esc(l2, 40)
        + "</i></span>"
    )


MSEP = '<span class="msep"></span>'


def _kpi(
    value: str, label: str, detail: str = "", tone: str = "", key: str = ""
) -> str:
    """``value`` and ``detail`` arrive as markup already built through :func:`ev_esc`."""
    return (
        '<div class="kpi"'
        + ((' data-series="' + ev_esc(key, 24) + '"') if key else "")
        + '><div class="kv'
        + ((" " + ev_esc(tone, 24)) if tone else "")
        + '">'
        + value
        + '</div><div class="kl">'
        + ev_esc(label, 80)
        + "</div>"
        + (('<div class="kd">' + detail + "</div>") if detail else "")
        + "</div>"
    )


def _gap_kpi(label: str, why: str, key: str = "") -> str:
    return _kpi(
        '<span class="kv-gap">n/a</span>',
        label,
        '<span class="kd-gap">' + ev_esc(why, 400) + "</span>",
        key=key,
    )


def _chip(group: str, value: str, label: str, count: int, sw: str = "") -> str:
    return (
        '<button type="button" class="chip" data-group="'
        + ev_esc(group, 24)
        + '" data-v="'
        + ev_esc(value, 60)
        + '" aria-pressed="false">'
        + (('<i class="sw ' + ev_esc(sw, 24) + '"></i>') if sw else "")
        + ev_esc(label, 60)
        + "<i>"
        + str(int(count))
        + "</i></button>"
    )


def _sec_block(
    title: str, body: str, count: object = None, note: str = "", open_: bool = False
) -> str:
    return (
        '<div class="apis"><details class="sec"'
        + (" open" if open_ else "")
        + '><summary><span class="chev" aria-hidden="true"></span>'
        + ev_esc(title, 80)
        + (
            ('<span class="cnt">' + str(int(count)) + "</span>")
            if count not in (None, "")
            else ""
        )
        + (('<span class="mt">' + ev_esc(note, 160) + "</span>") if note else "")
        + '</summary><div class="secbody">'
        + body
        + "</div></details></div>"
    )


def _empty(why: str) -> str:
    return '<p class="empty-note big">' + ev_esc(why, 400) + "</p>"


def _check(pill_cls: str, status: str, check: str, detail: str) -> str:
    return (
        "<li>"
        + _pill(pill_cls, status)
        + '<span class="ck" dir="auto">'
        + ev_esc(check, 120)
        + '</span><span class="cd" dir="auto">'
        + ev_esc(detail, 300)
        + "</span></li>"
    )


def _fmt(ms: object) -> str:
    return exchanges.fmt_ms(ms)


def _status_label(b: dict) -> str:
    if b.get("statusKnown") and b.get("status") is not None:
        return str(b.get("status"))
    if b.get("ok"):
        return "2xx (code not logged)"
    if b.get("ok") is None:
        return "never returned"
    return "failed (code not logged)"


def _status_pill(b: dict) -> str:
    ok = b.get("ok")
    return _pill(
        "p-ok" if ok else ("p-void" if ok is None else "p-def"), _status_label(b)
    )


# ── loading ────────────────────────────────────────────────────────────────────


def _none(reason: str, cases: list, extra: dict | None = None) -> dict:
    content = {
        "evidence": None,
        "report": None,
        "source": "none",
        "reason": str(reason),
        "disabled": [],
        "integrity": {},
        "profile": None,
        "cases_by_id": {
            str(c.get("tc_id") or ""): c for c in cases if isinstance(c, dict)
        },
        "by_case": {},
        "clock_missing": [],
    }
    content.update(extra or {})
    return {"error": None, "content": content}


def _flatten_case(case: dict) -> dict:
    """The join reads ``clock_offset_ms`` at the top; the lane stores it under ``evidence``."""
    out = dict(case)
    ev = case.get("evidence") if isinstance(case.get("evidence"), dict) else {}
    if "clock_offset_ms" not in out:
        out["clock_offset_ms"] = ev.get("clock_offset_ms")
    return out


def load_run_evidence(
    run_id: str, manifest: dict, cases: list, profile: object
) -> dict:
    """Read this run's ``evidence/`` files, parse them with *profile*, join them to *cases*.

    ``{"error": None, "content": {"evidence", "report", "source", "reason", "disabled",
    "integrity", ...}}``. ``source`` is ``"ndjson"`` (the app's own log was pulled),
    ``"logcat-only"`` (slices only) or ``"none"`` (nothing usable, with the reason).
    Never raises; arms the learned-value net on success (release it with :func:`release`).
    """
    cases = [c for c in (cases or []) if isinstance(c, dict)]
    scrub.forget_sensitive()
    try:
        manifest = manifest if isinstance(manifest, dict) else {}
        package = str(manifest.get("package") or "")
        if not getattr(settings, "qa_mobile_app_evidence", True):
            return _none(FLAG_OFF_REASON, cases)
        if profile is None:
            return _none(
                "no app log captured for "
                + (package or "(no package)")
                + ": no profile for this package",
                cases,
            )
        listed = run_store.list_evidence(run_id)
        files = listed.get("content") if isinstance(listed, dict) else None
        if listed.get("error") or not isinstance(files, dict):
            return _none(
                "no app log captured: " + str(listed.get("error") or "no evidence dir"),
                cases,
            )
        ndjson_text = None
        budget = int(MAX_LOAD_BYTES)
        skipped_slices: list = []
        if EVENTS_FILE in (files.get("") or []):
            read = run_store.read_evidence_text(run_id, None, EVENTS_FILE)
            if isinstance(read.get("content"), str) and read["content"].strip():
                ndjson_text = read["content"]
                budget -= len(ndjson_text.encode("utf-8", errors="replace"))
        logcat_lines: list = []
        # Slices in case order, and only while the budget lasts: a run with many
        # cases must not put every slice in memory at once. What is skipped is
        # NAMED, never silently absent.
        for tc_id in sorted(k for k in files if k):
            for name in sorted(files.get(tc_id) or []):
                if not (name.startswith("logcat-") and name.endswith(".txt")):
                    continue
                if budget <= 0:
                    skipped_slices.append(str(tc_id) + "/" + str(name))
                    continue
                read = run_store.read_evidence_text(run_id, tc_id, name)
                text = read.get("content")
                if isinstance(text, str) and text.strip():
                    size = len(text.encode("utf-8", errors="replace"))
                    if size > budget:
                        skipped_slices.append(str(tc_id) + "/" + str(name))
                        budget = 0
                        continue
                    budget -= size
                    logcat_lines.extend(text.splitlines())
        if ndjson_text is None and not logcat_lines:
            reason = str(manifest.get("events_reason") or "")
            return _none(
                "no app log captured for this run"
                + (
                    (": " + reason)
                    if reason
                    else " -- no slice and no event log on disk"
                ),
                cases,
                {
                    "profile": getattr(profile, "name", "")
                    or getattr(profile, "package", "")
                },
            )
        from tools.mobile_evidence import profiles as _profiles

        compiled = _profiles.compile(profile)
        compiled_content = (
            compiled.get("content") if isinstance(compiled, dict) else None
        )
        if not isinstance(compiled_content, dict):
            return _none("the profile could not be compiled", cases)
        flat_cases = [_flatten_case(c) for c in cases]
        built = evidence.build(
            flat_cases,
            profile,
            compiled_content,
            ndjson=ndjson_text.splitlines() if ndjson_text is not None else None,
            logcat=logcat_lines or None,
        )
        ev = built.get("content") if isinstance(built, dict) else None
        if built.get("error") or ev is None:
            return _none(
                "the app log could not be read: "
                + str(built.get("error") or "unknown"),
                cases,
            )
        report = ev.report
        scrub.learn_sensitive(report)
        source = "ndjson" if ndjson_text is not None else "logcat-only"
        integrity = ev.integrity()
        content = {
            "evidence": ev,
            "report": report,
            "source": source,
            "reason": ""
            if source == "ndjson"
            else str(
                manifest.get("events_reason")
                or "the app's own event log was not pulled"
            ),
            "disabled": list(integrity.get("disabledStreams") or []),
            "integrity": integrity,
            "profile": getattr(profile, "name", "") or getattr(profile, "package", ""),
            "rates": dict(getattr(profile, "rates", None) or {}),
            "rates_note": str(getattr(profile, "rates_note", "") or ""),
            "env_by_host": dict(getattr(profile, "env_by_host", None) or {}),
            "cases_by_id": {str(c.get("tc_id") or ""): c for c in cases},
            "flat_cases": flat_cases,
            "by_case": {},
            "clock_missing": list(integrity.get("clockNotRead") or []),
            "truncated_load": bool(skipped_slices),
            "skipped_slices": list(skipped_slices),
        }
        for c in flat_cases:
            tc_id = str(c.get("tc_id") or "")
            content["by_case"][tc_id] = _case_view(content, c)
        return {"error": None, "content": content}
    except Exception as exc:  # noqa: BLE001 - the contract is never to raise
        logger.exception("mobile_evidence.render.load_run_evidence failed")
        return _none("the app evidence could not be loaded: " + str(exc)[:200], cases)


def release() -> None:
    """Drop the learned values once the page is built."""
    scrub.forget_sensitive()


def _content(loaded: object) -> dict:
    body = loaded.get("content") if isinstance(loaded, dict) else None
    return (
        body if isinstance(body, dict) else {"source": "none", "reason": "not loaded"}
    )


def _has(loaded: object) -> bool:
    return _content(loaded).get("source") != "none"


def _case_view(content: dict, case: dict) -> dict:
    """Everything a card, a chip and a table need for ONE case, computed once."""
    ev = content["evidence"]
    turns = ev.for_case(case)
    llm = [c for t in turns for c in t.get("llm") or []]
    bindings = [b for t in turns for b in t.get("bindings") or []]
    tools = [x for t in turns for x in t.get("tools") or []]
    tin = tout = 0
    cost = 0.0
    unpriced = 0
    models: Counter = Counter()
    for call in llm:
        tok = call.get("tokens") or {}
        name = tok.get("model") or call.get("model") or "(unrecorded)"
        models[name] += 1
        tin += int(tok.get("in") or 0)
        tout += int(tok.get("out") or 0)
        priced = model.price(name, tok.get("in"), tok.get("out"), content.get("rates"))
        if priced is None:
            if tok:
                unpriced += 1
        else:
            cost += priced
    window = ev.window_of(str(case.get("tc_id") or "")) or {}
    return {
        "turns": turns,
        "llm": llm,
        "bindings": bindings,
        "tools": tools,
        "in": tin,
        "out": tout,
        "cost": cost if llm and unpriced < len(llm) else None,
        "unpriced": unpriced,
        "models": models,
        "exch": len(llm) + len(bindings) + len(tools),
        "window": window,
    }


def _view(loaded: object, tc_id: object) -> dict | None:
    return _content(loaded).get("by_case", {}).get(str(tc_id or ""))


# ── run-level fragments ────────────────────────────────────────────────────────


def facts_cells(loaded: object) -> list:
    """Runstrip cells: ``[(label, value_html, sub_html, tone)]``."""
    content = _content(loaded)
    if not _has(loaded):
        return [
            (
                "app evidence",
                "none",
                ev_esc(content.get("reason") or "not captured", 160),
                "void",
            )
        ]
    ev = content["evidence"]
    integrity = content.get("integrity") or {}
    cells = []
    env = integrity.get("envFromTraffic") or integrity.get("envClaimed")
    if env:
        cells.append(
            (
                "backend",
                ev_esc(env, 40),
                (
                    "manifest says "
                    + ev_esc(integrity.get("envClaimed"), 24)
                    + " — the traffic is the authority"
                )
                if integrity.get("envDisagrees")
                else "from the traffic"
                if integrity.get("envFromTraffic")
                else "as the app's manifest says",
                "gap" if integrity.get("envDisagrees") else "",
            )
        )
    served = Counter()
    for row in ev.models():
        served[row["model"]] += row["calls"]
    if served:
        names = ", ".join(ev_esc(name, 60) for name, _n in served.most_common(3))
        cells.append(
            (
                "model",
                names,
                str(sum(served.values()))
                + " model call"
                + ("" if sum(served.values()) == 1 else "s"),
                "",
            )
        )
    source = content.get("source")
    cells.append(
        (
            "evidence",
            "the app’s own event log" if source == "ndjson" else "logcat slices only",
            ev_esc(content.get("reason"), 160)
            if content.get("reason")
            else "pulled with run-as after the run",
            "ok" if source == "ndjson" else "gap",
        )
    )
    missing = content.get("clock_missing") or []
    if missing:
        cells.append(
            (
                "device clock",
                "not read on "
                + str(len(missing))
                + " case"
                + ("" if len(missing) == 1 else "s"),
                ev_esc(evidence.NO_CLOCK_NOTE, 160),
                "gap",
            )
        )
    return cells


def overview_tiles(loaded: object) -> list:
    content = _content(loaded)
    if not _has(loaded):
        return [
            _gap_kpi(
                "app evidence", content.get("reason") or "not captured", key="evidence"
            )
        ]
    ev = content["evidence"]
    totals = ev.totals()
    tiles = [
        _kpi(
            str(totals["llm"]),
            "LLM requests",
            "model round-trips the app made across the cases"
            + (
                " · " + str(totals["ambiguous"]) + " between two cases, on neither card"
                if totals.get("ambiguous")
                else ""
            ),
            key="llm",
        ),
        _kpi(
            str(totals["bindings"]),
            "API exchanges",
            "endpoint calls the app made",
            key="api",
        ),
        _kpi(str(totals["tools"]), "tool calls", "tools the model invoked", key="tool"),
        _kpi(
            format(int(totals["in"]), ","),
            "tokens in",
            format(int(totals["out"]), ",") + " out — as the provider reported them",
            key="tokens",
        ),
    ]
    spend = 0.0
    priced = unpriced = 0
    for row in ev.models():
        usd = model.price(row["model"], row["in"], row["out"], content.get("rates"))
        if usd is None:
            unpriced += row["calls"]
        else:
            priced += row["calls"]
            spend += usd
    if priced:
        detail = ev_esc(
            content.get("rates_note") or "at the profile's published rates", 200
        )
        if unpriced:
            detail += (
                " · "
                + str(unpriced)
                + " call"
                + ("" if unpriced == 1 else "s")
                + " unpriced"
            )
        tiles.append(
            _kpi(ev_esc(model.money(spend), 24), "estimated spend", detail, key="cost")
        )
    else:
        tiles.append(
            _gap_kpi(
                "estimated spend",
                "unpriced — no model in this run is in the profile's rates table"
                if totals["llm"]
                else "no model call to price",
                key="cost",
            )
        )
    return tiles


def _entry_row(b: dict) -> str:
    verb = str(b.get("verb") or "CALL")
    url = str(b.get("url") or b.get("target") or "")
    label = str(b.get("binding") or "")
    ok = b.get("ok")
    cls = "p-ok" if ok else ("p-void" if ok is None else "p-def")
    return _check(cls, _status_label(b), label or verb, verb + " " + url)


def session_block(loaded: object) -> str:
    """The app outside every case: start-up, sign-in, between-case work, ambiguous turns."""
    content = _content(loaded)
    if not _has(loaded):
        return ""
    ev = content["evidence"]
    body = ""
    total = 0
    for rec in ev.session():
        entry = rec.get("entry") or []
        failed = rec.get("failed") or []
        total += len(entry)
        rows = "".join(_entry_row(b) for b in entry[:MAX_ROWS])
        meta = (
            '<div class="metabits"><div class="mb"><span class="ml">window</span><span class="mv">'
            + ev_esc(evidence.stamp(rec.get("from")), 40)
            + " &rarr; "
            + ev_esc(evidence.stamp(rec.get("to")), 40)
            + '</span></div><div class="mb"><span class="ml">turns served</span><span class="mv">'
            + str(int(rec.get("turns") or 0))
            + '</span></div><div class="mb"><span class="ml">model calls</span><span class="mv">'
            + str(int(rec.get("llm") or 0))
            + '</span></div><div class="mb"><span class="ml">endpoint calls</span><span class="mv">'
            + str(int(rec.get("bindings") or 0))
            + "</span></div></div>"
        )
        body += _sec_block(
            "App run " + str(rec.get("appRunId") or "?"),
            meta
            + "<h4>Session entry</h4>"
            + (
                ('<ul class="checks">' + rows + "</ul>")
                if rows
                else _empty("no call outside a case in this app run")
            ),
            count=len(entry),
            note=str(len(entry)) + " entry call(s), " + str(len(failed)) + " failed",
        )
    amb = ev.ambiguous_records()
    if amb:
        rows = ""
        for rec in amb[:MAX_ROWS]:
            stream = str(rec.get("stream") or "")
            what = (
                rec.get("binding")
                or rec.get("model")
                or rec.get("tool")
                or rec.get("text")
                or rec.get("head")
                or stream
            )
            rows += _check("p-gap", stream, _clip(what, 80), evidence.AMBIGUOUS_NOTE)
        body += _sec_block(
            "Between two cases",
            '<ul class="checks">' + rows + "</ul>",
            count=len(amb),
            note="attributed to neither card — the device clock was not read for a neighbouring case",
        )
    if not body:
        body = _empty("every recorded event fell inside a case window")
    return _sec_block(
        "The app outside every case",
        body,
        count=total or None,
        note="start-up, sign-in and between-case work — the events that belong to no case card",
    )


def trust_block(loaded: object) -> str:
    """Can this capture be trusted: the recorder's account of itself."""
    content = _content(loaded)
    if not _has(loaded):
        return ""
    integrity = content.get("integrity") or {}
    rows = []
    if integrity.get("envDisagrees"):
        rows.append(
            _check(
                "p-def",
                "env",
                "the manifest and the traffic disagree",
                "the manifest says "
                + str(integrity.get("envClaimed"))
                + "; the traffic went to "
                + str(integrity.get("envFromTraffic"))
                + ". The traffic is the authority.",
            )
        )
    elif integrity.get("envFromTraffic") or integrity.get("envClaimed"):
        rows.append(
            _check(
                "p-ok",
                "env",
                "manifest and traffic agree",
                str(integrity.get("envFromTraffic") or integrity.get("envClaimed")),
            )
        )
    never = integrity.get("neverReturned") or []
    rows.append(
        _check(
            "p-ok" if not never else "p-gap",
            "calls",
            "requests that never came back",
            "none — every request has a logged response"
            if not never
            else str(len(never))
            + ": "
            + ", ".join(str(b.get("binding") or "?") for b in never[:6]),
        )
    )
    malformed = int(integrity.get("malformed") or 0)
    rows.append(
        _check(
            "p-ok" if not malformed else "p-gap",
            "lines",
            "lines the reader could not parse",
            str(malformed),
        )
    )
    if content.get("truncated_load"):
        skipped = list(content.get("skipped_slices") or [])
        rows.append(
            _check(
                "p-gap",
                "load",
                "evidence load capped at " + str(MAX_LOAD_BYTES) + " bytes",
                str(len(skipped))
                + " slice(s) not read: "
                + ", ".join(skipped[:6])
                + (" …" if len(skipped) > 6 else ""),
            )
        )
    disabled = content.get("disabled") or []
    if disabled:
        rows.append(
            _check(
                "p-gap",
                "streams",
                "streams this profile does not describe",
                "; ".join(
                    str(d[0]) + " (" + str(d[1]) + ")"
                    for d in disabled[:8]
                    if isinstance(d, (list, tuple)) and len(d) == 2
                ),
            )
        )
    rows.append(
        _check(
            "p-void",
            "turns",
            "how a turn boundary was decided",
            "from the app's own turn-start marker, one app run at a time"
            if integrity.get("turnsFromNarration")
            else "from the turn id the app stamped on each event"
            if content["evidence"].report.get("turns")
            else "one turn per case: the profile has no turn-start marker",
        )
    )
    rows.append(
        _check(
            "p-void",
            "source",
            "which capture this page read",
            "the app's own event log (events.ndjson), with the per-case logcat slices for the network lines"
            if content.get("source") == "ndjson"
            else "the per-case logcat slices only — "
            + str(content.get("reason") or ""),
        )
    )
    missing = content.get("clock_missing") or []
    rows.append(
        _check(
            "p-ok" if not missing else "p-gap",
            "clock",
            "device clock read at case start",
            "on every case"
            if not missing
            else "NOT on "
            + ", ".join(str(t) for t in missing[:6])
            + " — "
            + evidence.NO_CLOCK_NOTE,
        )
    )
    note = integrity.get("joinNote") or ""
    if note:
        rows.append(
            _check("p-void", "join", "how events were attributed to cases", str(note))
        )
    clipped = list(integrity.get("clipped") or [])
    inverted = list(integrity.get("inverted") or [])
    if clipped or inverted:
        rows.append(
            _check(
                "p-gap",
                "windows",
                "case windows clipped against a neighbour",
                "clipped: "
                + (", ".join(str(t) for t in clipped[:6]) or "none")
                + (
                    " — clipped to a POINT (ran inside a neighbour): "
                    + ", ".join(str(t) for t in inverted[:6])
                    if inverted
                    else ""
                ),
            )
        )
    return _sec_block(
        "Can this capture be trusted",
        '<ul class="checks">' + "".join(rows) + "</ul>",
        count=len(rows),
        note="the recorder’s account of itself — none of this is about the app under test",
    )


def _endpoint_rows(content: dict) -> list:
    """(verb, path) -> {calls, cases:set, statuses:Counter, samples:list} over paired turns."""
    agg: dict = {}
    for tc_id, view in (content.get("by_case") or {}).items():
        for b in view["bindings"]:
            url = str(b.get("url") or "")
            rest = url.split("://", 1)[1] if "://" in url else ""
            path = (
                ("/" + rest.split("/", 1)[1].split("?")[0])
                if "/" in rest
                else (url or str(b.get("target") or "(unknown)"))
            )
            key = ((b.get("verb") or "CALL").upper(), path)
            row = agg.setdefault(
                key,
                {
                    "verb": key[0],
                    "path": path,
                    "calls": 0,
                    "cases": set(),
                    "statuses": Counter(),
                    "samples": [],
                    "errors": 0,
                },
            )
            row["calls"] += 1
            row["cases"].add(tc_id)
            row["statuses"][_status_label(b)] += 1
            if b.get("ok") is False:
                row["errors"] += 1
            ms = b.get("derivedMs")
            if isinstance(ms, (int, float)):
                row["samples"].append(float(ms))
    return sorted(agg.values(), key=lambda r: (-r["calls"], r["path"]))


def apis_section(loaded: object) -> str:
    content = _content(loaded)
    if not _has(loaded):
        return _empty(
            "no app log captured, so no endpoint is known: "
            + str(content.get("reason") or "")
        )
    rows = _endpoint_rows(content)
    if not rows:
        return _empty("the app made no endpoint call inside any case window")
    hosts = (content.get("integrity") or {}).get("hosts") or []
    body = "".join(
        '<tr><td class="uc"><span class="cap">'
        + ev_esc(r["verb"], 12)
        + "</span> <code>"
        + ev_esc(r["path"], 160)
        + "</code></td><td>"
        + str(r["calls"])
        + "</td><td>"
        + str(len(r["cases"]))
        + "</td><td>"
        + ", ".join(
            ev_esc(label, 40) + " ×" + str(n)
            for label, n in r["statuses"].most_common()
        )
        + "</td></tr>"
        for r in rows[:MAX_ROWS]
    )
    hint = (
        '<p class="hint">real wire, host'
        + ("" if len(hosts) == 1 else "s")
        + " "
        + ", ".join(ev_esc(h, 80) for h in hosts)
        + "</p>"
        if hosts
        else ""
    )
    return (
        hint
        + '<div class="tablewrap"><table class="cov"><thead><tr><th scope="col">Endpoint</th>'
        '<th scope="col">Calls</th><th scope="col">Cases</th><th scope="col">Statuses</th></tr></thead><tbody>'
        + body
        + "</tbody></table></div>"
    )


def _series_tile(key: str, label: str, unit: str, samples: list) -> str:
    if not samples:
        return _gap_kpi(
            label,
            "no " + unit + " carried a derivable duration in this capture",
            key=key,
        )
    ordered = sorted(samples)
    p50, p90, p99 = (
        model.pct(ordered, 0.5),
        model.pct(ordered, 0.9),
        model.pct(ordered, 0.99),
    )
    detail = (
        '<span class="kstat">usually '
        + ev_esc(_fmt(p50), 16)
        + ", between "
        + ev_esc(_fmt(ordered[0]), 16)
        + " and "
        + ev_esc(_fmt(ordered[-1]), 16)
        + '</span><span class="kstat">across '
        + str(len(ordered))
        + " "
        + ev_esc(unit, 24)
        + "</span>"
        '<details class="kwhy"><summary>what this means</summary><p>'
        + ev_esc(evidence.DERIVED_NOTE, 400)
        + "</p><p>"
        + (
            "Precisely: p50 "
            + ev_esc(_fmt(p50), 16)
            + ", p90 "
            + ev_esc(_fmt(p90), 16)
            + ", p99 "
            + ev_esc(_fmt(p99), 16)
            if len(ordered) >= 8
            else str(len(ordered))
            + " measurements, too few for a percentile to mean anything: in full, "
            + ", ".join(ev_esc(_fmt(s), 16) for s in ordered)
        )
        + "</p></details>"
    )
    return _kpi(ev_esc(_fmt(p50), 16), label, detail, key=key)


def perf_extra(loaded: object) -> str:
    content = _content(loaded)
    if not _has(loaded):
        return _empty(
            "no app log captured, so nothing the app did could be timed: "
            + str(content.get("reason") or "")
        )
    ev = content["evidence"]
    spans = ev.spans()
    tiles = "".join(
        _series_tile(key, label, unit, spans.get(key) or [])
        for key, label, unit in SERIES
    )
    out = (
        '<p class="hint">' + ev_esc(evidence.DERIVED_NOTE, 400) + "</p>"
        '<div class="kpis">' + tiles + "</div>"
    )
    endpoints = ev.endpoints()
    if endpoints:
        rows = ""
        for r in sorted(
            endpoints, key=lambda r: -(model.pct(sorted(r["samples"]), 0.5) or 0)
        ):
            ordered = sorted(r["samples"])
            p50, p95, worst = (
                model.pct(ordered, 0.5),
                model.pct(ordered, 0.95),
                (ordered[-1] if ordered else None),
            )
            rows += (
                '<tr tabindex="0" data-spath="'
                + ev_esc(r["path"], 160)
                + '" data-scalls="'
                + str(r["calls"])
                + '" data-sp50="'
                + (str(p50) if p50 is not None else "-1")
                + '" data-sp95="'
                + (str(p95) if p95 is not None else "-1")
                + '" data-sworst="'
                + (str(worst) if worst is not None else "-1")
                + '" data-serr="'
                + str(r["errors"])
                + '"><td class="uc"><span class="cap">'
                + ev_esc(r["verb"], 12)
                + "</span> <code>"
                + ev_esc(r["path"], 160)
                + '</code></td><td class="num">'
                + str(r["calls"])
                + '</td><td class="num">'
                + ev_esc(_fmt(p50), 16)
                + '</td><td class="num">'
                + ev_esc(_fmt(p95), 16)
                + '</td><td class="num">'
                + ev_esc(_fmt(worst), 16)
                + '</td><td class="num">'
                + (str(r["errors"]) if r["errors"] else '<span class="mt">none</span>')
                + "</td></tr>"
            )
        out += _sec_block(
            "Which backend calls were slow",
            '<p class="hint">Sort any column. Typical is the middle time; slower runs is the time 19 calls in 20 came in under (p50 and p95).</p>'
            '<div class="tablewrap"><table class="cov sortable" id="perfendpoints"><thead><tr>'
            '<th scope="col" data-sort="path">Endpoint</th><th scope="col" data-sort="calls" class="num">Calls</th>'
            '<th scope="col" data-sort="p50" class="num">Typical</th><th scope="col" data-sort="p95" class="num">Slower runs</th>'
            '<th scope="col" data-sort="worst" class="num">Slowest</th><th scope="col" data-sort="err" class="num">Errors</th>'
            "</tr></thead><tbody>" + rows + "</tbody></table></div>",
            note="every endpoint the run reached, slowest first",
        )
    models = ev.models()
    if models:
        top = max((model.pct(sorted(m["samples"]), 0.5) or 0) for m in models) or 1.0
        items = ""
        buckets: Counter = Counter()
        for m in sorted(models, key=lambda m: -m["calls"]):
            p50 = model.pct(sorted(m["samples"]), 0.5)
            usd = model.price(m["model"], m["in"], m["out"], content.get("rates"))
            bucket = model.lat_bucket(p50) or "fast"
            buckets[bucket] += 1
            items += (
                '<li class="runrow"><span class="rstatic"><span class="rname"><b></b><code>'
                + ev_esc(m["model"], 80)
                + '</code><span class="rmeta">'
                + str(m["calls"])
                + " calls · "
                + format(int(m["in"]), ",")
                + " tokens in, "
                + format(int(m["out"]), ",")
                + " out"
                + (
                    (" · " + ev_esc(model.money(usd), 24))
                    if usd is not None
                    else " · unpriced"
                )
                + '</span></span><span class="rval">'
                + ev_esc(_fmt(p50), 16)
                + '</span><span class="rtrack"><i class="sw-lat-'
                + bucket
                + '" style="width:%.1f%%"></i></span></span></li>'
                % (max(1.0, (p50 or 0) / float(top) * 100.0))
            )
        legend = "".join(
            '<li><span class="legkey"><i class="sw sw-lat-'
            + key
            + '"></i><b>'
            + str(buckets.get(key, 0))
            + "</b>"
            + label
            + "</span></li>"
            for key, label, _c in model.LAT_BUCKETS
        )
        out += (
            '<figure class="hist"><figcaption><b>Which models were used</b><span>'
            + str(len(models))
            + " model"
            + ("" if len(models) == 1 else "s")
            + " · "
            + str(sum(m["calls"] for m in models))
            + ' calls · busiest first</span></figcaption><ul class="runs">'
            + items
            + '</ul><ul class="seglegend">'
            + legend
            + "</ul></figure>"
        )
    return out


# ── the toolbar and the card ───────────────────────────────────────────────────


def toolbar_groups(loaded: object, facts: list) -> list:
    content = _content(loaded)
    by_case = content.get("by_case") or {}
    models: Counter = Counter()
    sources: Counter = Counter()
    for f in facts:
        view = by_case.get(str(f.get("tc_id") or ""))
        if view and view["llm"]:
            for name in view["models"]:
                models[name] += 1
            sources["app"] += 1
        else:
            sources["none"] += 1
    groups = []
    if models:
        groups.append(
            (
                "Model",
                "".join(
                    _chip("model", name, name, n) for name, n in models.most_common()
                ),
            )
        )
    if _has(loaded):
        groups.append(
            (
                "Evidence",
                "".join(
                    _chip("src", key, label, sources[key])
                    for key, label in (
                        ("app", "the app’s own log"),
                        ("none", "no app evidence"),
                    )
                    if sources.get(key)
                ),
            )
        )
    return groups


def extra_sorts(loaded: object) -> list:
    if not _has(loaded):
        return []
    return [
        ("exch", "exchanges, most first"),
        ("tok", "tokens, most first"),
        ("cost", "cost, highest first"),
    ]


def card_data(loaded: object, tc_id: object) -> dict:
    view = _view(loaded, tc_id)
    if not view:
        return {"data-src": "none"} if _has(loaded) else {}
    return {
        "data-model": ", ".join(sorted(view["models"])) if view["models"] else "",
        "data-src": "app" if view["llm"] or view["bindings"] else "none",
        "data-exch": str(view["exch"]),
        "data-tok": str(view["in"] + view["out"]) if view["llm"] else "",
        "data-cost": ("%.6f" % view["cost"]) if view["cost"] is not None else "",
    }


def card_metrics(loaded: object, tc_id: object) -> list:
    view = _view(loaded, tc_id)
    if not view or not view["exch"]:
        return []
    bits = [
        MSEP,
        _metric(len(view["llm"]), "LLM"),
        _metric(len(view["bindings"]), "API"),
        _metric(len(view["tools"]), "tools"),
    ]
    if view["llm"]:
        bits += [
            MSEP,
            _metric_pair(
                format(view["in"], ","),
                "tok in",
                format(view["out"], ","),
                "out",
                "tok",
            ),
        ]
    if view["cost"] is not None:
        label = (
            "total cost"
            if not view["unpriced"]
            else "total cost (" + str(view["unpriced"]) + " unpriced)"
        )
        bits += [MSEP, _metric(model.money(view["cost"]), label, "cost")]
    return bits


def _turn_rows(view: dict, report: dict) -> list:
    """One row per app turn: (n, said, reply_ms, outcome_pill, answer)."""
    rows = []
    for n, turn in enumerate(view["turns"], 1):
        utt = (turn.get("utterances") or [None])[0]
        said = (utt or {}).get("text") or ""
        answers = turn.get("answers") or []
        errors = turn.get("errors") or []
        start = (utt or {}).get("ts")
        end = answers[0].get("ts") if answers else None
        reply_ms = (
            (end - start)
            if isinstance(start, (int, float))
            and isinstance(end, (int, float))
            and end >= start
            else None
        )
        if answers:
            outcome = _pill("p-ok", "answered")
        elif errors:
            outcome = _pill(
                "p-def",
                str(
                    (errors[0].get("detail") or {}).get("class")
                    or errors[0].get("kind")
                    or "failed"
                ),
            )
        elif turn.get("llm") or turn.get("bindings"):
            outcome = _pill("p-gap", "no reply recorded")
        else:
            outcome = _pill("p-void", "nothing recorded")
        rows.append(
            (n, said, reply_ms, outcome, answers[0].get("text") if answers else "")
        )
    return rows


def turns_table(loaded: object, tc_id: object) -> str:
    content = _content(loaded)
    if not _has(loaded):
        return _sec_block(
            "Turns",
            _empty(
                "no app log captured for this run: " + str(content.get("reason") or "")
            ),
            note="what the app did is not available",
        )
    view = _view(loaded, tc_id)
    if not view or not view["turns"]:
        return _sec_block(
            "Turns",
            _empty("no app turn fell inside this case's window"),
            note="nothing the app logged was attributed to this case",
        )
    rows = ""
    for n, said, reply_ms, outcome, answer in _turn_rows(view, content["report"]):
        rows += (
            '<tr><td class="n">'
            + str(n)
            + '</td><td class="said" dir="auto">'
            + (
                ("<q>" + ev_esc(_clip(said), 80) + "</q>")
                if said
                else '<span class="mt">no utterance logged</span>'
            )
            + '</td><td class="num">'
            + ev_esc(_fmt(reply_ms) if reply_ms is not None else "—", 16)
            + '</td><td class="outc">'
            + outcome
            + '</td><td class="said" dir="auto">'
            + (
                ("<q>" + ev_esc(_clip(answer), 80) + "</q>")
                if answer
                else '<span class="mt">no reply text</span>'
            )
            + "</td></tr>"
        )
    table = (
        '<div class="tablewrap"><table class="cov turns"><thead><tr><th scope="col">#</th>'
        '<th scope="col">The app heard</th><th scope="col" class="num">Reply in</th>'
        '<th scope="col">Outcome</th><th scope="col">The app said</th></tr></thead><tbody>'
        + rows
        + "</tbody></table></div>"
        '<p class="hint">Reply in: the app’s own turn-start line to its answer line, from the timestamps it wrote itself.</p>'
    )
    return _sec_block(
        "Turns",
        table,
        count=len(view["turns"]),
        note="what the app heard, how long it took, what came back",
        open_=True,
    )


def _bar(ms: object, longest: float, kind: str) -> str:
    if not isinstance(ms, (int, float)) or ms < 0 or not longest:
        return ""
    return (
        '<span class="seqbar k-'
        + ev_esc(kind, 12)
        + '" title="'
        + str(int(ms))
        + " ms of the "
        + str(int(longest))
        + ' ms longest step in this case"><i style="width:%.1f%%"></i></span>'
        % max(3.0, min(100.0, ms / float(longest) * 100.0))
    )


def _seq_key(rec: dict) -> float | None:
    ts = rec.get("ts")
    return float(ts) if isinstance(ts, (int, float)) else None


def sequence_items(
    loaded: object, tc_id: object, trace_rows: list | None = None
) -> list:
    """``[(key, rank, kind, inner_html, clock_label, bar)]`` for ``exchanges.seqlist``.

    The app's records and the lane's own actions merge on one absolute clock: the
    records carry the device's epoch ms, an action its host ``at`` corrected by the
    case's clock offset (the same correction the join applied to the window).
    """
    content = _content(loaded)
    view = _view(loaded, tc_id)
    if not _has(loaded) or not view:
        return []
    case = (content.get("cases_by_id") or {}).get(str(tc_id or "")) or {}
    offset = _flatten_case(case).get("clock_offset_ms")
    offset = (
        int(offset)
        if isinstance(offset, (int, float)) and not isinstance(offset, bool)
        else 0
    )
    items: list = []
    durations = []
    for turn in view["turns"]:
        for stream in ("llm", "bindings", "tools"):
            for rec in turn.get(stream) or []:
                ms = rec.get("derivedMs")
                if isinstance(ms, (int, float)):
                    durations.append(float(ms))
    longest = max(durations) if durations else 0.0
    multi = len(view["turns"]) > 1
    for n, turn in enumerate(view["turns"], 1):
        first_key = None
        for stream in evidence.STREAMS:
            for rec in turn.get(stream) or []:
                key = _seq_key(rec)
                if key is not None and (first_key is None or key < first_key):
                    first_key = key
        if multi and first_key is not None:
            utt = (turn.get("utterances") or [None])[0]
            items.append(
                (
                    first_key,
                    exchanges.SEQ_RANK["turn"],
                    "turn",
                    _guard(exchanges.seq_turn(n, (utt or {}).get("text") or "")),
                )
            )
        for rec in turn.get("utterances") or []:
            key = _seq_key(rec)
            if key is not None:
                items.append(
                    (
                        key,
                        exchanges.SEQ_RANK["user"],
                        "user",
                        _guard(exchanges.seq_user(str(rec.get("text") or ""))),
                    )
                )
        for rec in turn.get("llm") or []:
            key = _seq_key(rec)
            if key is None:
                continue
            x = exchanges.llm_exchange(rec)
            prompts = "".join(
                exchanges.codebox(exchanges.body_text(p)) or ""
                for p in (rec.get("promptMessages") or [])
            )
            if prompts:
                chars = sum(
                    len(exchanges.body_text(p))
                    for p in (rec.get("promptMessages") or [])
                )
                items.append(
                    (
                        key,
                        exchanges.SEQ_RANK["prompt"],
                        "prompt",
                        _guard(
                            exchanges.seq_prompt(
                                chars,
                                prompts,
                                head="request to " + str(x.get("path") or ""),
                                meta=str(len(rec.get("promptMessages") or []))
                                + " msgs",
                            )
                        ),
                    )
                )
            items.append(
                (
                    key,
                    exchanges.SEQ_RANK["llm"],
                    "llm",
                    _guard(exchanges.seq_exchange(x)),
                    None,
                    _bar(rec.get("derivedMs"), longest, "llm"),
                )
            )
        for rec in turn.get("tools") or []:
            key = _seq_key(rec)
            if key is not None:
                items.append(
                    (
                        key,
                        exchanges.SEQ_RANK["call"],
                        "call",
                        _guard(exchanges.seq_exchange(exchanges.tool_exchange(rec))),
                        None,
                        _bar(rec.get("derivedMs"), longest, "tool"),
                    )
                )
        for rec in turn.get("bindings") or []:
            key = _seq_key(rec)
            if key is not None:
                items.append(
                    (
                        key,
                        exchanges.SEQ_RANK["api"],
                        "api",
                        _guard(exchanges.seq_exchange(exchanges.binding_exchange(rec))),
                        None,
                        _bar(rec.get("derivedMs"), longest, "api"),
                    )
                )
        drawn = {
            (str(b.get("verb") or "").upper(), str(b.get("url") or "").split("?")[0])
            for b in turn.get("bindings") or []
        }
        for rec in turn.get("runlog") or []:
            key = _seq_key(rec)
            if key is None:
                continue
            if rec.get("lane") == "net" and rec.get("url"):
                if (
                    (rec.get("verb") or "").upper(),
                    str(rec.get("url")).split("?")[0],
                ) in drawn:
                    continue
                items.append(
                    (
                        key,
                        exchanges.SEQ_RANK["net"],
                        "api",
                        _guard(exchanges.seq_exchange(exchanges.network_exchange(rec))),
                    )
                )
            else:
                items.append(
                    (
                        key,
                        exchanges.SEQ_RANK["log"],
                        str(rec.get("lane") or "log"),
                        _guard(
                            exchanges.seq_log(
                                str(rec.get("lane") or "log"),
                                str(rec.get("head") or ""),
                                str(rec.get("body") or ""),
                                bad=bool(rec.get("bad")),
                            )
                        ),
                    )
                )
        for rec in turn.get("flowStates") or []:
            key = _seq_key(rec)
            if key is None:
                continue
            bits = " · ".join(
                str(k) + "=" + str(v)
                for k, v in rec.items()
                if k
                not in ("seq", "turnId", "appRunId", "tool", "note", "ts", "derivedMs")
            )
            head = "flow " + str(rec.get("tool") or "") + ": " + bits
            items.append(
                (
                    key,
                    exchanges.SEQ_RANK["flow"],
                    "flow",
                    _guard(
                        exchanges.seq_flow(
                            head,
                            body=(
                                '<p class="seqfull" dir="auto">'
                                + ev_esc(rec.get("note"), 400)
                                + "</p>"
                            )
                            if rec.get("note")
                            else "",
                        )
                    ),
                )
            )
        for rec in turn.get("cards") or []:
            key = _seq_key(rec)
            if key is not None:
                items.append(
                    (
                        key,
                        exchanges.SEQ_RANK["card"],
                        "card",
                        _guard(
                            exchanges.seq_card(
                                str(rec.get("kind") or "card"),
                                bool(rec.get("replaced")),
                            )
                        ),
                    )
                )
        for rec in turn.get("notes") or []:
            key = _seq_key(rec)
            if key is not None:
                items.append(
                    (
                        key,
                        exchanges.SEQ_RANK["log"],
                        "note",
                        _guard(exchanges.seq_note(str(rec.get("text") or ""))),
                    )
                )
        for rec in turn.get("errors") or []:
            key = _seq_key(rec)
            if key is None or rec.get("kind") in ("binding", "llm", "tool"):
                continue  # already on their own rows
            detail = rec.get("detail") or {}
            items.append(
                (
                    key,
                    exchanges.SEQ_RANK["agent"],
                    "agent",
                    _guard(
                        exchanges.seq_log(
                            "agent",
                            str(
                                detail.get("message") or detail.get("class") or "failed"
                            ),
                            bad=True,
                        )
                    ),
                )
            )
        for rec in turn.get("answers") or []:
            key = _seq_key(rec)
            if key is not None:
                items.append(
                    (
                        key,
                        exchanges.SEQ_RANK["reply"],
                        "reply",
                        _guard(exchanges.seq_reply(ev_esc(rec.get("text"), 4000))),
                    )
                )
    # The lane's own actions, on the same clock.
    trace = [t for t in (case.get("trace") or [])[:MAX_ROWS] if isinstance(t, dict)]
    rows = list(trace_rows or [])
    for i, entry in enumerate(trace):
        at = entry.get("at")
        if not isinstance(at, (int, float)) or isinstance(at, bool) or at <= 0:
            continue
        key = float(at) * 1000.0 + offset
        row = rows[i] if i < len(rows) and isinstance(rows[i], dict) else {}
        op = str(row.get("op") or (entry.get("action") or {}).get("op") or "?")
        line = str(row.get("action") or op)
        outcome = str(row.get("outcome") or entry.get("outcome") or "")
        kind = "tap" if op in ("tap", "press", "back") else "step"
        inner = exchanges.seq_step(op, line) + (
            _pill(
                "p-ok"
                if outcome in ("ok", "done") or outcome.endswith("_pass")
                else "p-gap",
                outcome,
            )
            if outcome
            else ""
        )
        items.append((key, exchanges.SEQ_RANK[kind], kind, _guard(inner)))
    return items
