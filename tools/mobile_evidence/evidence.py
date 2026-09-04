"""Join what the LANE did to what the APP did, for one mobile run -- keyed on the CASE.

A run is witnessed twice and neither witness sees the whole thing. The lane's checkpoints
know the case: which tc_id, when it started and ended (host clock), every replayed action
with its ``at`` stamp. The app's own log knows the model round-trips, every endpoint call,
every tool call, the flow-engine state and the cards it pushed -- and nothing about cases.
This module is the seam.

THE JOIN IS BY TIME, AND IT IS HONEST ABOUT ITS CLOCKS. The lane types nothing the app
narrates, so the reference's text match is unavailable; the only shared axis is time.

  * A case's WINDOW is ``[started, updated]`` on the host clock, moved onto the device
    clock by the case's ``clock_offset_ms`` (device = host + offset), which the lane
    measured at case start.
  * When the offset is ``None`` (the device clock could not be read) the window is
    widened by ``TOLERANCE_MS`` on each side and then CLIPPED at the midpoint to each
    neighbouring case's window -- neighbours being adjacent by ``started`` -- so two
    windows can never overlap. The report says "device clock not read".
  * An app turn belongs to the ONE case whose window its ``[firstTs, lastTs]`` overlaps.
    A turn that overlaps no window but lies between two cases is ``ambiguous`` -- "could
    belong to an adjacent case" -- and lands on NEITHER card. A turn before the first or
    after the last case is ``outside``. Nothing is guessed, nothing is dropped in silence.
  * The turn text is used only as CONFIRMATION when the case's trace carries a typed
    literal -- it never does on a file this tree wrote, so the usual verdict is "not
    confirmed", which is not a mismatch.
  * A profile with no turn-start marker yields no turns at all; then every record whose
    clock falls inside a case's window is attributed to ONE synthetic turn per case, and
    the join says so.

Three-state ``ok`` (True / False / None) is preserved end to end: an unanswered request is
None, never False. Nothing here raises to a caller: :func:`build` returns
``{"error", "content"}``.
"""

from __future__ import annotations

import calendar
import datetime
import json

from tools.mobile_evidence import grammar

SCHEMA = "qa-agents.mobile-evidence.evidence/1"

#: How far a case window is widened when the device clock was not read (D6).
TOLERANCE_MS = 2000

#: Everything the parser emits per turn, in the shape the report reads.
STREAMS = (
    "llm",
    "bindings",
    "tools",
    "notes",
    "errors",
    "utterances",
    "answers",
    "flowStates",
    "cards",
    "runlog",
)

DERIVED_NOTE = (
    "Not a stopwatch reading. The app writes a line when it sends the request and another "
    "when the answer arrives, and this is the gap between those two lines -- so it also "
    "contains anything else the app did in between"
)

AMBIGUOUS_NOTE = "ambiguous -- could belong to an adjacent case"
NO_CLOCK_NOTE = "device clock not read -- attribution by host clock, +/-%d s" % (
    TOLERANCE_MS // 1000
)
ONE_TURN_NOTE = (
    "the profile carries no turn-start marker, so every record inside this case's window "
    "is shown as one turn per case"
)


# ── clocks ─────────────────────────────────────────────────────────────────────


def _epoch_ms(year, mon, day, hh, mm, ss, ms, offset_ms):
    """A logcat wall-clock stamp as epoch milliseconds, given the device's UTC offset."""
    try:
        dt = datetime.datetime(year, mon, day, hh, mm, ss)
    except ValueError:  # 29 Feb in a non-leap candidate year
        return None
    return calendar.timegm(dt.timetuple()) * 1000 + ms - offset_ms


def capture_offset(source, default=0):
    """The device's UTC offset, from the structured log's own ``utcOffsetMs`` field."""
    if source is None:
        return default
    try:
        for raw in grammar._lines_of(source):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("utcOffsetMs") is not None:
                return int(rec["utcOffsetMs"])
    except (OSError, TypeError, ValueError):
        return default
    return default


def _resolve_year(parts, offset_ms, anchor):
    """The year a logcat stamp belongs to: the candidate nearest the capture's own clock."""
    if anchor is None:
        anchor = int(datetime.datetime.now().timestamp() * 1000)
    base = datetime.datetime.fromtimestamp(
        anchor / 1000.0 + offset_ms / 1000.0, tz=datetime.timezone.utc
    ).year
    best = None
    for year in (base - 1, base, base + 1):
        ms = _epoch_ms(year, *parts, offset_ms=offset_ms)
        if ms is None:
            continue
        if best is None or abs(ms - anchor) < abs(best - anchor):
            best = ms
    return best


def slice_window(lines, offset_ms, anchor):
    """(first ms, last ms, stamped lines) across one logcat slice."""
    lo = hi = None
    seen = 0
    for line in grammar._lines_of(lines):
        m = grammar.RE_SLICE_TS.match(line)
        if not m:
            continue
        parts = tuple(int(x) for x in m.groups())
        ms = _resolve_year(parts, offset_ms, anchor)
        if ms is None:
            continue
        seen += 1
        lo = ms if lo is None else min(lo, ms)
        hi = ms if hi is None else max(hi, ms)
    return lo, hi, seen


def normalise_clock(report, offset_ms, anchor=None):
    """Make every ``clock`` value numeric: a logcat capture stores ``MM-DD HH:MM:SS.mmm``."""
    clock = report.get("clock") or {}
    for run, table in clock.items():
        for key, value in list(table.items()):
            if isinstance(value, str):
                m = grammar.RE_SLICE_TS.match(value)
                table[key] = (
                    _resolve_year(tuple(int(x) for x in m.groups()), offset_ms, anchor)
                    if m
                    else None
                )
    for r in report.get("appRuns") or []:
        for key in ("firstTs", "lastTs"):
            value = r.get(key)
            if isinstance(value, str):
                m = grammar.RE_SLICE_TS.match(value)
                r[key] = (
                    _resolve_year(tuple(int(x) for x in m.groups()), offset_ms, anchor)
                    if m
                    else None
                )
    return report


def fence(report, window):
    """Which app runs the run window touches (overlap, not containment), and which it does not."""
    runs = report.get("appRuns") or []
    lo, hi = (window or {}).get("from"), (window or {}).get("to")
    if lo is None or hi is None:
        return (
            list(runs),
            [],
            "no case in this run carries a window, so every app run in the capture is shown",
        )
    inside, outside = [], []
    for r in runs:
        a, b = r.get("firstTs"), r.get("lastTs")
        if a is None or b is None:
            outside.append(r)
            continue
        (inside if (a <= hi and b >= lo) else outside).append(r)
    return inside, outside, ""


def _ts_of(report, run, seq):
    value = (report.get("clock") or {}).get(run, {}).get(str(seq))
    return value if isinstance(value, (int, float)) else None


def _derived_ms(report, run, rec):
    """How long a request took, DERIVED from two logged clocks -- never measured."""
    a = _ts_of(report, run, rec.get("seq"))
    b = _ts_of(report, run, rec.get("endSeq"))
    if a is None or b is None or b < a:
        return None
    return b - a


def _stamped(report, run, rec):
    return dict(
        rec,
        ts=_ts_of(report, run, rec.get("seq")),
        derivedMs=_derived_ms(report, run, rec),
    )


# ── the case windows ───────────────────────────────────────────────────────────


def _ms(value):
    """A host ``time.time()`` (seconds) or an epoch-ms value, as epoch ms, or None."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    # A seconds stamp is ~1.7e9; an ms stamp ~1.7e12.
    return int(number * 1000) if number < 1e11 else int(number)


INVERTED_NOTE = (
    "this case ran inside a neighbour's window, so its own window was clipped to a "
    "point -- the app's events in that span are attributed to the neighbour"
)


def case_windows(cases, tolerance_ms=TOLERANCE_MS):
    """One device-clock window per case, exact when the offset is known, clipped when not.

    Returns a list sorted by ``started`` of ``{tc_id, lo, hi, exact, clipped, note}``.
    A case with no usable ``started``/``updated`` has ``lo``/``hi`` None and joins nothing.
    """
    rows = []
    for case in cases or []:
        if not isinstance(case, dict):
            continue
        started = _ms(case.get("started"))
        updated = _ms(case.get("updated"))
        if started is None and updated is None:
            rows.append(
                {
                    "tc_id": str(case.get("tc_id") or ""),
                    "lo": None,
                    "hi": None,
                    "exact": False,
                    "clipped": False,
                    "note": "this case carries no start or end time, so no window can be drawn",
                }
            )
            continue
        started = started if started is not None else updated
        updated = updated if updated is not None else started
        if updated < started:
            started, updated = updated, started
        offset = case.get("clock_offset_ms")
        exact = isinstance(offset, (int, float)) and not isinstance(offset, bool)
        rows.append(
            {
                "tc_id": str(case.get("tc_id") or ""),
                "started": started,
                "updated": updated,
                "lo": started + (int(offset) if exact else -tolerance_ms),
                "hi": updated + (int(offset) if exact else tolerance_ms),
                "exact": exact,
                "clipped": False,
                "note": "" if exact else NO_CLOCK_NOTE,
            }
        )
    timed = sorted((r for r in rows if r["lo"] is not None), key=lambda r: r["started"])
    # Clip the WIDENED windows at the midpoint to each neighbour, so two windows never
    # overlap. An exact window is never widened, so it never needs clipping -- but a
    # widened neighbour may reach into it, and that side IS clipped.
    for i, row in enumerate(timed):
        if row["exact"]:
            continue
        if i > 0:
            prev = timed[i - 1]
            mid = (prev["updated"] + row["started"]) // 2
            if row["lo"] < mid:
                row["lo"], row["clipped"] = mid, True
        if i + 1 < len(timed):
            nxt = timed[i + 1]
            mid = (row["updated"] + nxt["started"]) // 2
            if row["hi"] > mid:
                row["hi"], row["clipped"] = mid, True
        # Nested cases (one started and ended inside another) clip to lo > hi: an
        # inverted window matches nothing and reads as "the app did nothing". Clamp
        # to a point and SAY so, rather than joining nothing in silence.
        row["inverted"] = False
        if row["hi"] < row["lo"]:
            row["hi"], row["inverted"] = row["lo"], True
            row["note"] = (row["note"] + " " if row["note"] else "") + INVERTED_NOTE
    return timed + [r for r in rows if r["lo"] is None]


# ── the join ───────────────────────────────────────────────────────────────────


class Join:
    """The mapping, and everything that had to be true for it to be one.

    ``pairs``       [(tc_id, turnId)]
    ``ambiguous``   [(turnId, note)] -- between two cases, on neither card
    ``outside``     [(turnId, why)]  -- before the first or after the last case
    ``unclocked``   [turnId]         -- the capture holds no clock for the turn
    ``empty_cases`` [tc_id]          -- cases whose window holds no turn
    ``confirmed`` / ``unconfirmed`` / ``mismatched`` -- turn-text confirmation counts
    ``synthetic``   True when turns were made one-per-case (no turn-start marker)
    """

    __slots__ = (
        "pairs",
        "ambiguous",
        "outside",
        "unclocked",
        "empty_cases",
        "confirmed",
        "unconfirmed",
        "mismatched",
        "synthetic",
        "note",
        "by_case",
    )

    def __init__(
        self,
        pairs,
        ambiguous=(),
        outside=(),
        unclocked=(),
        empty_cases=(),
        confirmed=0,
        unconfirmed=0,
        mismatched=0,
        synthetic=False,
        note="",
    ):
        self.pairs = list(pairs)
        self.ambiguous = list(ambiguous)
        self.outside = list(outside)
        self.unclocked = list(unclocked)
        self.empty_cases = list(empty_cases)
        self.confirmed = confirmed
        self.unconfirmed = unconfirmed
        self.mismatched = mismatched
        self.synthetic = synthetic
        self.note = note
        self.by_case: dict = {}
        for tc_id, tid in self.pairs:
            self.by_case.setdefault(tc_id, []).append(tid)

    @property
    def ok(self):
        return not self.ambiguous and not self.mismatched

    def turn_ids(self, tc_id):
        return list(self.by_case.get(str(tc_id), []))


def _typed_texts(case):
    """Typed literals a trace may carry. The lane never stores them; this stays empty."""
    out = []
    for entry in (case or {}).get("trace") or []:
        action = (entry or {}).get("action") if isinstance(entry, dict) else None
        if isinstance(action, dict) and action.get("op") == "type":
            text = action.get("text")
            if isinstance(text, str) and text and text != "***":
                out.append(text.strip())
    return out


def join(report, cases, windows=None):
    """Attribute every app turn to exactly one case window, or say why not."""
    windows = windows if windows is not None else case_windows(cases)
    timed = [w for w in windows if w.get("lo") is not None]
    by_tc = {str(c.get("tc_id") or ""): c for c in (cases or []) if isinstance(c, dict)}
    said = {}
    for u in report.get("utterances") or []:
        if u.get("turnId"):
            said[u["turnId"]] = (u.get("text") or "").strip()

    turns = list(report.get("turns") or [])
    if not turns:
        # D2: no turn-start marker -> one synthetic turn per case.
        pairs = [(w["tc_id"], "case/" + w["tc_id"]) for w in timed if w["tc_id"]]
        return Join(
            pairs,
            empty_cases=[],
            synthetic=True,
            note=ONE_TURN_NOTE
            if pairs
            else "no case window and no app turn: nothing to join",
        )

    pairs, ambiguous, outside, unclocked = [], [], [], []
    confirmed = unconfirmed = mismatched = 0
    claimed_cases = set()
    for t in turns:
        run = t.get("appRunId")
        a = _ts_of(report, run, t.get("firstSeq"))
        b = _ts_of(report, run, t.get("lastSeq"))
        tid = t.get("turnId")
        if a is None or b is None:
            unclocked.append(tid)
            continue
        hits = [w for w in timed if a <= w["hi"] and b >= w["lo"]]
        if len(hits) == 1:
            tc_id = hits[0]["tc_id"]
            pairs.append((tc_id, tid))
            claimed_cases.add(tc_id)
            typed = _typed_texts(by_tc.get(tc_id))
            heard = said.get(tid, "")
            if not typed:
                unconfirmed += 1
            elif heard in typed:
                confirmed += 1
            else:
                mismatched += 1
            continue
        if len(hits) > 1:
            ambiguous.append(
                (tid, AMBIGUOUS_NOTE + " (overlaps %d windows)" % len(hits))
            )
            continue
        if timed and a > timed[0]["lo"] and b < timed[-1]["hi"]:
            ambiguous.append((tid, AMBIGUOUS_NOTE))
        else:
            outside.append(
                (
                    tid,
                    "served at %s, outside every case window of this run" % stamp(a),
                )
            )
    empty = [
        w["tc_id"] for w in timed if w["tc_id"] and w["tc_id"] not in claimed_cases
    ]
    note_bits = []
    if any(not w["exact"] for w in timed):
        note_bits.append(NO_CLOCK_NOTE)
    if ambiguous:
        note_bits.append(
            "%d turn(s) fell between two cases and are shown on neither card: %s"
            % (len(ambiguous), ", ".join(str(t) for t, _n in ambiguous))
        )
    if unclocked:
        note_bits.append(
            "%d turn(s) carry no clock and could not be placed" % len(unclocked)
        )
    return Join(
        pairs,
        ambiguous,
        outside,
        unclocked,
        empty,
        confirmed,
        unconfirmed,
        mismatched,
        False,
        "; ".join(note_bits),
    )


# ── the joined run ─────────────────────────────────────────────────────────────


class Evidence:
    """One run, joined: the report, the fence, the case windows, the mapping."""

    __slots__ = (
        "report",
        "cases",
        "windows",
        "included",
        "excluded",
        "fence_note",
        "join",
        "source",
        "_by_turn",
        "_session",
    )

    def __init__(self, report, cases, windows, included, excluded, fence_note, jn):
        self.report = report
        self.cases = list(cases or [])
        self.windows = windows
        self.included = included
        self.excluded = excluded
        self.fence_note = fence_note
        self.join = jn
        self.source = (report.get("run") or {}).get("source")
        self._by_turn = None
        self._session = None

    def window_of(self, tc_id):
        for w in self.windows:
            if w.get("tc_id") == str(tc_id):
                return w
        return None

    def _synthetic_turn_of(self, run, rec):
        ts = _ts_of(self.report, run, rec.get("seq"))
        if ts is None:
            return None
        for w in self.windows:
            if w.get("lo") is not None and w["lo"] <= ts <= w["hi"]:
                return "case/" + w["tc_id"]
        return None

    def _index(self):
        if self._by_turn is not None:
            return self._by_turn
        runs = {r.get("appRunId") for r in self.included}
        idx: dict = {}
        for stream in STREAMS:
            for rec in self.report.get(stream) or []:
                run = rec.get("appRunId") or (rec.get("detail") or {}).get("appRunId")
                if run not in runs:
                    continue
                tid = rec.get("turnId")
                if not tid and self.join.synthetic:
                    tid = self._synthetic_turn_of(run, rec)
                if not tid:
                    continue
                slot = idx.setdefault(tid, {k: [] for k in STREAMS})
                slot[stream].append(_stamped(self.report, run, rec))
        self._by_turn = idx
        return idx

    def turn(self, turn_id):
        if not turn_id:
            return None
        return self._index().get(turn_id)

    def for_case(self, case):
        """One evidence dict per turn attributed to ``case``, in turn order; [] when none."""
        tc_id = str((case or {}).get("tc_id") or "")
        out = []
        for tid in self.join.turn_ids(tc_id):
            found = self.turn(tid)
            if found is not None:
                out.append(dict(found, turnId=tid))
        return out

    @staticmethod
    def _entry_key(verb, url):
        base = (url or "").split("?", 1)[0].rstrip("/")
        scheme, sep, rest = base.partition("://")
        if sep:
            host, slash, tail = rest.partition("/")
            base = scheme.lower() + "://" + host.lower() + slash + tail
        return ((verb or "").upper(), base)

    def session(self):
        """Per app run: the work the app did outside every turn (start-up, sign-in, between cases)."""
        if self._session is not None:
            return self._session
        out = []
        for r in self.included:
            run = r.get("appRunId")
            rec = {
                "appRunId": run,
                "runIndex": r.get("runIndex"),
                "from": r.get("firstTs"),
                "to": r.get("lastTs"),
                "turns": r.get("turns") or 0,
                "llm": r.get("llm") or 0,
                "bindings": r.get("bindings") or 0,
                "tools": r.get("tools") or 0,
                "errors": r.get("errors") or 0,
                "manifest": None,
                "entry": [],
                "failed": [],
                "other": [],
            }
            for m in self.report.get("manifests") or []:
                if m.get("appRunId") == run:
                    rec["manifest"] = m
            out.append(rec)
        index = {r["appRunId"]: r for r in out}
        for stream in STREAMS:
            for x in self.report.get(stream) or []:
                run = x.get("appRunId") or (x.get("detail") or {}).get("appRunId")
                if run not in index:
                    continue
                tid = x.get("turnId") or (
                    self._synthetic_turn_of(run, x) if self.join.synthetic else None
                )
                if tid:
                    continue
                slot = index[run]
                x = _stamped(self.report, run, x)
                if stream == "bindings":
                    slot["entry"].append(x)
                    if x.get("ok") is False:
                        slot["failed"].append(x)
                elif stream == "runlog" and x.get("lane") == "net" and x.get("url"):
                    outcome = (x.get("outcome") or "").upper()
                    ok = (
                        True
                        if outcome == "SUCCESS"
                        else (False if outcome == "ERROR" else None)
                    )
                    key = self._entry_key(x.get("verb"), x.get("url"))
                    drawn = {
                        self._entry_key(b.get("verb"), b.get("url"))
                        for b in slot["entry"]
                        if b.get("binding") != "network log only"
                    }
                    if key not in drawn:
                        call = dict(
                            x, binding="network log only", ok=ok, statusKnown=False
                        )
                        slot["entry"].append(call)
                        if ok is False:
                            slot["failed"].append(call)
                elif stream == "errors":
                    if x.get("kind") != "binding":
                        slot["failed"].append(x)
                else:
                    slot["other"].append(x)
        for slot in out:
            slot["entry"].sort(key=lambda b: b.get("seq") or 0)
            slot["failed"].sort(key=lambda b: b.get("seq") or 0)
        self._session = out
        return out

    def ambiguous_records(self):
        """Every record of a turn the join called ambiguous -- for the run-level block."""
        ids = {tid for tid, _n in self.join.ambiguous}
        out = []
        for stream in STREAMS:
            for rec in self.report.get(stream) or []:
                if rec.get("turnId") in ids:
                    run = rec.get("appRunId") or (rec.get("detail") or {}).get(
                        "appRunId"
                    )
                    out.append(dict(_stamped(self.report, run, rec), stream=stream))
        out.sort(key=lambda r: (r.get("ts") or 0, r.get("seq") or 0))
        return out

    def mine(self):
        return {tid for _tc, tid in self.join.pairs}

    def spans(self):
        """Derived duration samples, in milliseconds, by kind, over the paired turns."""
        mine = self.mine()
        out = {
            "llm": [],
            "llmfail": [],
            "api": [],
            "tool": [],
            "turn": [],
            "turnfail": [],
        }

        def tid_of(rec):
            tid = rec.get("turnId")
            if not tid and self.join.synthetic:
                run = rec.get("appRunId")
                tid = self._synthetic_turn_of(run, rec)
            return tid

        for kind, stream in (("llm", "llm"), ("api", "bindings"), ("tool", "tools")):
            for rec in self.report.get(stream) or []:
                if tid_of(rec) not in mine:
                    continue
                ms = _derived_ms(self.report, rec.get("appRunId"), rec)
                if ms is None:
                    continue
                if kind == "llm" and rec.get("error"):
                    out["llmfail"].append(float(ms))
                else:
                    out[kind].append(float(ms))
        said, heard, threw = {}, {}, {}
        for u in self.report.get("utterances") or []:
            if u.get("turnId") in mine:
                said[u["turnId"]] = _ts_of(self.report, u.get("appRunId"), u.get("seq"))
        for a in self.report.get("answers") or []:
            if a.get("turnId") in mine:
                heard.setdefault(
                    a["turnId"], _ts_of(self.report, a.get("appRunId"), a.get("seq"))
                )
        for x in self.report.get("errors") or []:
            if x.get("turnId") in mine:
                threw[x["turnId"]] = _ts_of(
                    self.report, x.get("appRunId"), x.get("seq")
                )
        for tid, start in said.items():
            if start is None:
                continue
            end, bucket = heard.get(tid), "turn"
            if end is None:
                end, bucket = threw.get(tid), "turnfail"
            if end is not None and end >= start:
                out[bucket].append(float(end - start))
        return out

    def endpoints(self):
        """Every endpoint reached in a paired turn, with its derived timings."""
        mine = self.mine()
        agg: dict = {}
        for b in self.report.get("bindings") or []:
            tid = b.get("turnId") or (
                self._synthetic_turn_of(b.get("appRunId"), b)
                if self.join.synthetic
                else None
            )
            if tid not in mine:
                continue
            url = b.get("url") or ""
            rest = url.split("://", 1)[1] if "://" in url else ""
            path = (
                ("/" + rest.split("/", 1)[1].split("?")[0])
                if "/" in rest
                else (url or "(unknown)")
            )
            key = ((b.get("verb") or "CALL").upper(), path)
            r = agg.setdefault(
                key,
                {
                    "verb": key[0],
                    "path": path,
                    "calls": 0,
                    "errors": 0,
                    "samples": [],
                    "binding": b.get("binding"),
                },
            )
            r["calls"] += 1
            if b.get("ok") is False:
                r["errors"] += 1
            ms = _derived_ms(self.report, b.get("appRunId"), b)
            if ms is not None:
                r["samples"].append(float(ms))
        return list(agg.values())

    def models(self):
        """Every model that answered in a paired turn, with its derived timings."""
        mine = self.mine()
        agg: dict = {}
        for call in self.report.get("llm") or []:
            tid = call.get("turnId") or (
                self._synthetic_turn_of(call.get("appRunId"), call)
                if self.join.synthetic
                else None
            )
            if tid not in mine:
                continue
            tok = call.get("tokens") or {}
            name = tok.get("model") or call.get("model") or "(unrecorded)"
            r = agg.setdefault(
                name, {"model": name, "calls": 0, "in": 0, "out": 0, "samples": []}
            )
            r["calls"] += 1
            r["in"] += tok.get("in") or 0
            r["out"] += tok.get("out") or 0
            ms = _derived_ms(self.report, call.get("appRunId"), call)
            if ms is not None:
                r["samples"].append(float(ms))
        return list(agg.values())

    def totals(self):
        """What the paired turns spent; ``ambiguous`` and ``unplaceable`` named separately."""
        mine = self.mine()
        amb = {tid for tid, _n in self.join.ambiguous}
        tin = tout = calls = binds = tools = ambiguous = unplaceable = 0

        def tid_of(rec):
            tid = rec.get("turnId")
            if not tid and self.join.synthetic:
                tid = self._synthetic_turn_of(rec.get("appRunId"), rec)
            return tid

        for call in self.report.get("llm") or []:
            tid = tid_of(call)
            if tid is None:
                unplaceable += 1
                continue
            if tid in amb:
                ambiguous += 1
                continue
            if tid not in mine:
                continue
            calls += 1
            tok = call.get("tokens") or {}
            tin += tok.get("in") or 0
            tout += tok.get("out") or 0
        for b in self.report.get("bindings") or []:
            tid = tid_of(b)
            if tid is None:
                unplaceable += 1
            elif tid in mine:
                binds += 1
        for t in self.report.get("tools") or []:
            tid = tid_of(t)
            if tid is None:
                unplaceable += 1
            elif tid in mine:
                tools += 1
        return {
            "llm": calls,
            "in": tin,
            "out": tout,
            "total": tin + tout,
            "bindings": binds,
            "tools": tools,
            "ambiguous": ambiguous,
            "unplaceable": unplaceable,
        }

    def excluded_totals(self):
        mine = self.mine()
        tin = tout = calls = binds = 0
        for call in self.report.get("llm") or []:
            tid = call.get("turnId")
            if tid is None or tid in mine:
                continue
            calls += 1
            tok = call.get("tokens") or {}
            tin += tok.get("in") or 0
            tout += tok.get("out") or 0
        for b in self.report.get("bindings") or []:
            tid = b.get("turnId")
            if tid is not None and tid not in mine:
                binds += 1
        return {
            "runs": len(self.excluded),
            "llm": calls,
            "total": tin + tout,
            "bindings": binds,
        }

    def integrity(self):
        """What the capture says about ITSELF: configs, checkpoints, never-returned calls, gaps."""
        runs = {r.get("appRunId") for r in self.included}
        run = self.report.get("run") or {}
        configs = [
            _stamped(self.report, c["appRunId"], c)
            for c in (self.report.get("configs") or [])
            if c.get("appRunId") in runs
        ]
        checkpoints = [
            c
            for c in (self.report.get("checkpoints") or [])
            if c.get("appRunId") in runs
        ]
        never = [
            _stamped(self.report, b["appRunId"], b)
            for b in (self.report.get("bindings") or [])
            if b.get("appRunId") in runs
            and b.get("endSeq") is None
            and not b.get("orphanResponse")
        ]
        return {
            "configs": configs,
            "checkpoints": checkpoints,
            "neverReturned": never,
            "unresolved": self.report.get("unresolvedBindings") or [],
            "malformed": run.get("malformedLines") or 0,
            "envClaimed": run.get("envClaimed"),
            "envFromTraffic": run.get("envFromTraffic"),
            "envDisagrees": bool(run.get("envDisagrees")),
            "hosts": run.get("hosts") or [],
            "source": run.get("source"),
            "turnsFromNarration": bool(run.get("turnsFromNarration")),
            "disabledStreams": list(run.get("disabled") or []),
            "clockNotRead": [
                w["tc_id"]
                for w in self.windows
                if w.get("lo") is not None and not w["exact"]
            ],
            "clipped": [w["tc_id"] for w in self.windows if w.get("clipped")],
            "inverted": [w["tc_id"] for w in self.windows if w.get("inverted")],
            "joinNote": self.join.note,
        }


# ── entry points ───────────────────────────────────────────────────────────────


def empty_report():
    """The report of a run whose capture was never recorded: no streams, ``noCapture``."""
    return {
        "schema": grammar.SCHEMA,
        "run": {"source": None, "noCapture": True},
        "appRuns": [],
    }


def build(cases, profile, compiled, *, report=None, ndjson=None, logcat=None) -> dict:
    """Parse (or take) the app report and join it to ``cases``. Never raises.

    ``{"error", "content": Evidence}``. A run with no capture at all yields an Evidence
    over :func:`empty_report`, whose fence note says so -- the page then draws the lane's
    own rows and nothing else.
    """
    try:
        if report is None:
            if ndjson is None and logcat is None:
                report = empty_report()
                note = (
                    "this run holds no app capture -- no structured log and no logcat were "
                    "recorded -- so what the lane did is shown and what the app did is not "
                    "available for any case"
                )
            else:
                parsed = grammar.build(profile, compiled, ndjson=ndjson, logcat=logcat)
                if parsed.get("error"):
                    return {"error": parsed["error"], "content": None}
                report = parsed["content"]
                note = ""
        else:
            note = ""
        if not isinstance(report, dict):
            return {"error": "the app report is not a mapping", "content": None}
        offset = capture_offset(ndjson) if ndjson is not None else 0
        anchor = None
        for r in report.get("appRuns") or []:
            ts = r.get("lastTs")
            if isinstance(ts, (int, float)):
                anchor = ts if anchor is None else max(anchor, ts)
        windows = case_windows(cases)
        if anchor is None:
            timed = [w for w in windows if w.get("lo") is not None]
            anchor = timed[-1]["hi"] if timed else None
        normalise_clock(report, offset, anchor)
        timed = [w for w in windows if w.get("lo") is not None]
        window = {
            "from": min(w["lo"] for w in timed) if timed else None,
            "to": max(w["hi"] for w in timed) if timed else None,
            "cases": len(timed),
            "offsetMs": offset,
        }
        included, excluded, fence_note = fence(report, window)
        if (report.get("run") or {}).get("noCapture"):
            fence_note = note or fence_note
        jn = join(report, cases, windows)
        return {
            "error": None,
            "content": Evidence(
                report, cases, windows, included, excluded, fence_note, jn
            ),
        }
    except Exception as exc:  # noqa: BLE001 - the contract is never to raise
        return {
            "error": "could not join the capture to the run: " + str(exc)[:200],
            "content": None,
        }


def stamp(ms, offset_ms=0):
    """A capture timestamp as the device would have shown it. Empty for a missing one."""
    if not isinstance(ms, (int, float)):
        return ""
    return datetime.datetime.fromtimestamp(
        ms / 1000.0 + offset_ms / 1000.0, tz=datetime.timezone.utc
    ).strftime("%d %b %H:%M:%S")
