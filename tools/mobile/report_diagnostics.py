"""Diagnostics rendering for the report: device facts, performance, findings, accessibility, environment, logs, capture tables and the about section."""

from __future__ import annotations

import time
from collections import Counter

from tools.mobile import charter as charter_mod
from tools.mobile import (
    platform_info,
    run_finalize,
    screen_audit,
    step_timing,
    timings_view,
)
from tools.mobile.report_media import CLIP_SCALE_NOTE, _image_notes_html
from tools.mobile.report_text import (
    LAT_BUCKETS,
    _percentile,
    _short_stamp,
    _stamp,
    _text,
    esc,
    fmt_ms,
)
from tools.mobile.report_verdict import (
    coverage_phrase,
    error_classes,
    error_phrase,
    explore_stop,
)
from tools.mobile.report_widgets import _kpi, _PageData, _Run, _sechead
from tools.mobile_evidence import render as ev_render


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
    page: _PageData = _PageData(),
) -> str:
    loaded = page.loaded
    items = _run_cells(run_id, manifest, partial, page.coverage)
    locale_cell = _locale_cell(manifest)
    if locale_cell:
        items.append(locale_cell)
    if loaded:
        items.extend(ev_render.facts_cells(loaded))
    items.extend(_lease_cells(lease))
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


def _run_cells(
    run_id: str, manifest: dict, partial: bool, coverage: dict | None
) -> list:
    """The five always-present cells of the run facts strip."""
    return [
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


def _lease_cells(lease: dict) -> list:
    """The lease holder and takeover cells, each only when the lease names one."""
    holder = _text(lease.get("session_id"), 40)
    displaced = _text(lease.get("taken_over_from"), 40)
    items: list = []
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
    return items


def _perf_tile(values: list, title: str, noun: str, why: str) -> str:
    """One KPI tile: the median, the spread and the count of ``values``."""
    return _kpi(
        esc(fmt_ms(_percentile(values, 0.5)), 16),
        title,
        '<span class="kstat">usually '
        + esc(fmt_ms(_percentile(values, 0.5)), 16)
        + ", between "
        + esc(fmt_ms(min(values)), 16)
        + " and "
        + esc(fmt_ms(max(values)), 16)
        + '</span><span class="kstat">across '
        + str(len(values))
        + noun
        + why,
    )


def _perf_tiles(step_ms: list, walls: list) -> list:
    tiles = [
        _perf_tile(
            step_ms,
            "How long an action took",
            " actions</span>",
            '<details class="kwhy"><summary>what this means</summary><p>measured by the replay '
            "clock around one action — the tap or the wait itself, not the model's planning turn, "
            "which happens in the tester's own chat</p></details>",
        ),
    ]
    if walls:
        tiles.append(
            _perf_tile(
                [w for _f, w in walls],
                "How long a case took",
                " cases</span>",
                '<details class="kwhy"><summary>what this means</summary><p>the sum of that case\'s '
                "measured actions, a FLOOR rather than the whole truth: the turns the model spent "
                "planning between actions are nobody's measurement here</p></details>",
            )
        )
    return tiles


def _perf_run_rows(walls: list) -> str:
    top = max(w for _f, w in walls) or 1
    ordered = sorted(walls, key=lambda pair: -pair[1])
    return "".join(
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


def _perf_legend(walls: list) -> str:
    buckets = Counter(f["lat"] for f, _w in walls)
    return "".join(
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


def _perf_figure(walls: list) -> str:
    return (
        '<figure class="hist"><figcaption><b>How long a case took</b><span>'
        + str(len(walls))
        + " case"
        + ("" if len(walls) == 1 else "s")
        + ', each one shown · click one to open it</span></figcaption><ul class="runs">'
        + _perf_run_rows(walls)
        + '</ul><ul class="seglegend">'
        + _perf_legend(walls)
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
    tiles = _perf_tiles(step_ms, walls)
    figure = _perf_figure(walls) if walls else ""
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


def _replayed_turns(explore: dict, turns: int) -> int:
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
    return max(replayed, recorded)


def _spoken_turns(notes: list) -> set:
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
    return spoke


def _findings_lede(
    explore: dict, spoken: int, replayed: int, stop: str, terms: str
) -> str:
    silent = max(0, replayed - spoken)
    return (
        "Goal: "
        + (esc(explore.get("goal"), 400) or "(not recorded)")
        + ". "
        + str(spoken)
        + " finding"
        + ("" if spoken == 1 else "s")
        + " recorded over "
        + str(replayed)
        + " turn"
        + ("" if replayed == 1 else "s")
        + (
            ", and "
            + str(silent)
            + " turn"
            + ("" if silent == 1 else "s")
            + " recorded none — every turn is asked for one, so those are "
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
    replayed = _replayed_turns(explore, turns)
    spoke = _spoken_turns(notes)
    stop = _text(explore_stop(body), 40)
    # The run's TERMS, including every default the tester did not set: a
    # silent default is a run whose terms nobody can reconstruct afterwards.
    # `charter.describe_terms` is the ONE producer of this sentence, and it
    # returns "" for a run with no charter -- every run recorded before step 4
    # -- so those pages render exactly as they did.
    terms = charter_mod.describe_terms(explore.get("charter"))
    lede = _findings_lede(explore, len(spoke), replayed, stop, terms)
    return _sechead(
        "findings",
        "What exploring found",
        "one line per turn",
        lede,
        _findings_table(notes),
        level=3,
    )


def _findings_table(notes: list) -> str:
    """The one-row-per-turn findings table, or the empty note."""
    rows = "".join(
        '<tr><td class="num">'
        + esc(note.get("turn"), 8)
        + '</td><td dir="auto">'
        + esc(note.get("note"), 600)
        + "</td></tr>"
        for note in notes
    )
    return (
        '<div class="tablewrap"><table class="cov"><thead><tr>'
        '<th scope="col" class="num">Turn</th>'
        '<th scope="col">What the turn reported</th>'
        "</tr></thead><tbody>" + rows + "</tbody></table></div>"
        if rows
        else '<p class="empty-note big">No turn recorded a finding.</p>'
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


def _a11y_screen_row(screen_id: str, result: object, counts: Counter) -> str:
    """One screen's table rows, tallying what it adds into ``counts``."""
    if not isinstance(result, dict) or not result.get("present"):
        counts["unaudited"] += 1
        return (
            '<tr><td class="num">'
            + esc(screen_id, 40)
            + '</td><td class="cap">not audited</td><td dir="auto">'
            + "this screen was stored before the accessibility audit existed, "
            "so no finding here is evidence about it" + "</td></tr>"
        )
    if result.get("auditable"):
        counts["audited"] += 1
        if not result.get("complete"):
            counts["partial"] += 1
        if result.get("density_source") != screen_audit.DENSITY_DEVICE:
            counts["assumed"] += 1
    else:
        counts["unauditable"] += 1
    counts["total"] += len(
        [f for f in (result.get("findings") or []) if isinstance(f, dict)]
    )
    return _a11y_rows(screen_id, result)


def _a11y_lede(counts: Counter) -> str:
    total = counts["total"]
    audited = counts["audited"]
    unauditable = counts["unauditable"]
    partial = counts["partial"]
    unaudited = counts["unaudited"]
    assumed = counts["assumed"]
    return (
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
    counts = Counter()
    rows = []
    for screen_id in sorted(str(key) for key in library.keys()):
        screen = library.get(screen_id)
        if not isinstance(screen, dict):
            continue
        rows.append(_a11y_screen_row(screen_id, screen.get("accessibility"), counts))
    if not rows:
        return ""
    lede = _a11y_lede(counts)
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


#: The sentence an OLD manifest gets. A run created before this field existed genuinely
#: does not know its image, and the page says exactly that. It NEVER falls back to
#: ``settings`` or to a live ``sdk_locator.avd_system_image()`` read: both answer with
#: TODAY's image, which is a fabricated fact about a finished run -- the
#: success-shaped absence this repository has paid for before.
SYSTEM_IMAGE_ABSENT = (
    "not recorded for this run: it was created before the image was snapshotted into "
    "the manifest, and today's setting is not evidence of what ran then"
)


def _env_section(manifest: dict, loaded: dict | None = None) -> str:
    """The Environment & provenance section body.

    Reads the manifest and ``platform_info.SUPPORT``; DERIVES nothing that a producer
    already owns. The system image comes from the manifest SNAPSHOT and from nowhere else.
    """
    book = manifest if isinstance(manifest, dict) else {}
    image = str(book.get("system_image") or "")
    rows = [
        _env_row(
            "System image",
            esc(image, 120)
            if image
            else '<span class="cap none">' + esc(SYSTEM_IMAGE_ABSENT, 300) + "</span>",
            "snapshotted at run creation from sdk_locator.avd_system_image(); HTTPS body "
            "decryption is only possible on a rootable image"
            if image
            else "",
        ),
        _env_row("Device", esc(_device_sub(book), 120)),
        _env_host_row(),
        _env_row(
            "Rendered",
            esc(_short_stamp(time.time()), 40),
            "when this page was BUILT, which is not when the run happened",
        ),
    ]
    return '<div class="runstrip">' + "".join(rows) + "</div>"


def _env_row(label: str, value: str, sub: str = "") -> str:
    return (
        '<div class="envrow"><span class="rfl">'
        + esc(label, 60)
        + '</span><span class="rfv">'
        + value
        + ("<small>" + esc(sub, 300) + "</small>" if sub else "")
        + "</span></div>"
    )


def _env_host_row() -> str:
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
    return _env_row(
        "Host platform",
        esc(platform_info.raw_platform() or "(unknown)", 60),
        platform_info.support_statement(host),
    )


def _logs_section(manifest: object, facts: object, turns: int) -> str:
    """Findings, the explore-lane stop reason, and the device-error breakdown.

    The breakdown reads :func:`error_classes` -- the SAME call the overview KPI reads. It
    does not count rows of its own; two derivations of one number drift, and the pin
    monkeypatches the producer and requires both to move.
    """
    book = manifest if isinstance(manifest, dict) else {}
    body = _findings_section(book, turns) or ""
    stop = explore_stop(book) or run_finalize.stop_text(book)
    if stop:
        body += '<div class="guard"><p class="gapterm">why this run stopped</p>'
        body += '<p class="gsub">' + esc(stop, 300) + "</p></div>"
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


def _timings_part(manifest: object) -> str:
    """The Step timings Diagnostics part, or ``""`` when none were recorded.

    Reads ``manifest["step_timings"]``, bounded to the newest
    ``step_timing.MAX_STEP_TIMING_ROWS`` rows; the dropped count is stated."""
    raw = (manifest if isinstance(manifest, dict) else {}).get("step_timings")
    kept, dropped = step_timing.bound_rows(raw if isinstance(raw, list) else [])
    summary = timings_view.summarize(kept)
    body = timings_view.html_section(summary)
    if not body:
        return ""
    if dropped:
        body += "<p>" + esc(str(dropped) + " older steps are not shown.", 80) + "</p>"
    return _diag_part(
        "step-timings", "Step timings", timings_view.summary_line(summary), body
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


def _evidence_html(loaded: dict | None) -> str:
    """The app's own capture, summarised: its tiles, the session it came from,
    and the trust checks -- including why nothing was read (the flag is off,
    the load hit its byte cap). The old overview carried these.
    """
    tiles = "".join(ev_render.overview_tiles(loaded)) if loaded else ""
    return (
        (('<div class="kpis run">' + tiles + "</div>") if tiles else "")
        + (ev_render.session_block(loaded) + ev_render.trust_block(loaded))
        if loaded
        else ""
    )


def _network_html(facts: list, loaded: dict | None, cases: list, manifest: dict) -> str:
    """The network, API and capture sections plus the perf block, when any apply."""
    return (
        ev_render.apis_section(loaded)
        + ev_render.network_section(cases)
        + ev_render.capture_section(cases, manifest)
        + _perf(facts, loaded)
        if _has_network(facts, loaded)
        else ""
    )


def _diag_head(capture: str, notes: str, evidence: str, network: str) -> str:
    """The first four diagnostics parts."""
    return (
        _diag_part(
            "screen-capture",
            "Screen capture",
            "what the element reader kept and dropped",
            capture,
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
    )


def _diag_tail(logs: str, a11y: str, timings: str, env: str) -> str:
    """The last diagnostics parts: logs, accessibility, timings, environment."""
    return (
        _diag_part("logs", "Logs and findings", "", logs)
        + _diag_part("diag-a11y", "Accessibility", "", a11y)
        + timings
        + _diag_part("env", "Environment", "what this run ran on", env)
    )


def _diagnostics_html(run: _Run, screens: object, lease: dict, partial: bool) -> str:
    """The collapsed diagnostics. Built after the journey and the case list, so
    every image note those pictures raised is in the book by now.
    """
    a11y = _a11y_section(screens)
    head = _diag_head(
        _capture_table(run.library),
        _image_notes_html(),
        _evidence_html(run.loaded),
        _network_html(run.facts, run.loaded, run.cases, run.manifest),
    )
    env = _facts_strip(
        run.run_id,
        run.manifest,
        lease,
        partial,
        _PageData(loaded=run.loaded, coverage=run.coverage),
    ) + _env_section(run.manifest, run.loaded)
    return head + _diag_tail(
        _logs_section(run.manifest, run.facts, len(run.facts)),
        a11y,
        _timings_part(run.manifest),
        env,
    )
