"""Pins the rendered report against the run store, and exits non-zero on any miss.

WHY THIS IS NOT A TEST: it runs against a REAL run directory on a tester's
machine, after a real run, which no unit test can do. The suite proves the
renderer is correct on fixtures; this proves the artifact on disk is complete.

THE SHAPE THAT MATTERS. A pin that a TRUNCATED page can satisfy is worse than
no pin, because it certifies the artifact it failed to read. So every
completeness pin here is an EQUALITY or a terminator, never a containment:

* ``cases_exactly_once`` -- the set of ``data-tc`` values in the page EQUALS the
  set of ``tc_id``s in the store, and no id appears twice. A page that dropped
  half its cards fails on the equality; a page that duplicated one fails on the
  count. Two independent halves, because one mutation cannot prove both.
* ``totals_match`` -- the page's ``data-*`` totals are compared against a tally
  RECOMPUTED from the store, not against the page's own numbers.
* ``card_count_matches_total`` -- the parsed card count equals ``data-total``.
* ``terminator_present`` -- the document ends with the ``qa-report-end`` marker
  carrying the card count. Truncation removes it.

OUTPUT GOES TO ``sys.stdout.write``, not ``print`` (house rule: no bare
``print``) and not ``logging``. This is a CLI whose stdout IS its product -- the
programme spec asks for its output to be pasted as evidence -- and ``logging``
would route it to stderr under a configuration the CALLER owns, which would
make the required evidence depend on the caller's log setup.
"""

from __future__ import annotations

import logging
import sys
from html.parser import HTMLParser
from pathlib import Path

from tools.mobile import report, run_store

logger = logging.getLogger(__name__)

PIN_CASES = "cases_exactly_once"
PIN_TOTALS = "totals_match"
PIN_CARD_COUNT = "card_count_matches_total"
PIN_TERMINATOR = "terminator_present"
PIN_NO_MARKERS = "no_untrusted_markers"
PIN_NO_XML = "no_raw_xml"
PIN_NO_SCRIPT = "no_script_or_handler"
PIN_NO_ASSET = "no_external_asset"
PIN_NO_SECRET = "no_secret_marker"
PIN_SIZE = "page_under_8mb"
PIN_CRASH = "crashes_disclosed"
PIN_CAPTURE_TIER = "capture_tier_valid"

PINS = (
    PIN_CASES,
    PIN_TOTALS,
    PIN_CARD_COUNT,
    PIN_TERMINATOR,
    PIN_NO_MARKERS,
    PIN_NO_XML,
    PIN_NO_SCRIPT,
    PIN_NO_ASSET,
    PIN_NO_SECRET,
    PIN_SIZE,
    PIN_CRASH,
    PIN_CAPTURE_TIER,
)

#: A forged guard note is as dangerous as a real tag, so both are refused.
FORBIDDEN_MARKERS = ("untrusted_content", "SECURITY NOTE:")

#: The raw uiautomator dump has no route into the page. If any of these appears,
#: something bypassed ``perception`` entirely.
RAW_XML = ("<hierarchy", "<node ", 'bounds="[', "NAF=")

#: One self-contained FOLDER, bar the shell's typefaces: the report's own
#: recordings live beside index.html under the media directory and travel
#: with it, so they are NOT a break -- the image tag already left this
#: tuple when the lane began storing PNGs, and video and source are judged
#: the same way, by SOURCE, in :func:`_pin_links`, because a needle over
#: raw text cannot tell a relative sibling from a remote fetch. A frame, an
#: object, an embed or an @import still would break silently for a tester
#: offline, which is exactly when a report is read. These are FETCH constructs, not URL strings, and the
#: difference was measured rather than assumed: a screen that legitimately
#: displays "https://example.com" would make a URL-text pin red on a HEALTHY
#: tree, and a guard that is red on a healthy tree gets deleted. Every needle
#: here needs a raw "<" that escaping makes unreachable from app text;
#: "@import" is CSS-only and kept because a real one is a real offline break.
#: ``<link`` and ``<script`` are NOT needles any more: the shell carries one
#: script and three font links by design, so both are checked for IDENTITY
#: instead -- see :func:`_pin_script` and :func:`_pin_links`.
#: ``<img`` is NOT here any more. The lane stores a PNG per observation and the
#: report inlines it as a ``data:`` URI, which is self-contained -- the property
#: this tuple exists to protect -- so an absence pin here would forbid the
#: artifact rather than guard it. Images are checked for IDENTITY instead, in
#: :func:`_pin_links`, exactly as the shell's one script and three font links
#: already are. The remaining four are still absences: none of them has a
#: legitimate form on this page.
EXTERNAL = ("<iframe", "<object", "<embed", "@import")

#: Tags whose ``src`` the page may carry as a PATH, and the one prefix that
#: makes such a source portable: a path inside the report's own media folder.
#: Anything absolute, protocol-relative, or climbing out of the folder does
#: not travel when the folder is zipped, and fails.
#:
#: ``img`` is NOT here, and its absence is the rule rather than an omission:
#: an image on this page is a ``data:`` payload, judged for IDENTITY against
#: :data:`SHOT_SRC` below. Two different portability rules for two different
#: artifacts, each named, rather than one rule that would let a PNG be a path
#: or a clip be inlined.
MEDIA_TAGS = ("video", "source")

#: The one ``src`` prefix an image on this page may carry. Anything else is a
#: FETCH: a path into the run directory dies the moment the report is emailed,
#: which is the ordinary way a tester reads it.
#:
#: NOT a second literal. It is the EMITTER's constant, bound here, because a
#: guard that restates the value it guards stops guarding on the day the value
#: changes -- and this module already imports ``report``, so the definition can
#: only live over there without a cycle. ``report.SHOT_SRC is SHOT_SRC`` is
#: pinned in tests/mobile/test_mobile_report_shots.py.
SHOT_SRC = report.SHOT_SRC

#: The renderer never JSON-dumps a trace and never prints an action's text,
#: so a QUOTED "secret" key has no route into the page. Quoted deliberately:
#: an app label carrying the bare word is ESCAPED, not dumped, so it can
#: never produce these forms -- while a bare-word pin would go red on a
#: screen titled "Secret questions".
SECRET_TOKENS = ('"secret"', "'secret'")

USAGE = "usage: python -m tools.mobile.report_selfcheck <run_id> [--html PATH]\n"


class _Page(HTMLParser):
    """Structure only: cards, the summary attrs, the terminator, style text.

    Parsed rather than grepped on purpose -- a malformed tag becomes a
    structural difference instead of a substring that happens to match.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list = []
        #: ``tc_id -> data-crash``, collected beside the cards so the crash pin
        #: can be an EQUALITY against the store rather than a containment.
        self.crashes: dict = {}
        self.summary: dict = {}
        self.end: dict = {}
        self.styles: list = []
        self.scripts = 0
        self.script_text: list = []
        self.links: list = []
        #: ``(tag, src)`` for every media tag, so the asset pin can judge
        #: the SOURCE it would actually fetch, not the text around it.
        self.media: list = []
        #: Every ``img`` ``src``, so the asset pin can check IDENTITY.
        self.images: list = []
        self.handlers: list = []
        self._in_style = False
        self._in_script = False

    def handle_starttag(self, tag, attrs) -> None:
        pairs = {
            str(name).lower(): ("" if value is None else str(value))
            for name, value in attrs
        }
        if tag == "script":
            self.scripts += 1
            self._in_script = True
        if tag == "link":
            self.links.append(pairs.get("href", ""))
        # Only a tag that CARRIES a src fetches anything. The
        # <video> start tag does not: its fetch is the child
        # <source>. Recording the parent scored an empty src as
        # off-folder and failed the pin on every real clip.
        if tag in MEDIA_TAGS and "src" in pairs:
            self.media.append((tag, pairs["src"]))
        if tag == "img":
            self.images.append(pairs.get("src", ""))
        if tag == "style":
            self._in_style = True
        for name in pairs:
            if name.startswith("on"):
                self.handlers.append(tag + "@" + name)
        if "data-tc" in pairs:
            self.cards.append(pairs["data-tc"])
            self.crashes[pairs["data-tc"]] = pairs.get("data-crash", "")
        if pairs.get("id") == report.SUMMARY_ID:
            self.summary = pairs
        if pairs.get("id") == report.END_ID:
            self.end = pairs

    def handle_endtag(self, tag) -> None:
        if tag == "style":
            self._in_style = False
        if tag == "script":
            self._in_script = False

    def handle_data(self, data) -> None:
        if self._in_style:
            self.styles.append(data)
        if self._in_script:
            self.script_text.append(data)


def _pin(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "ok": bool(ok), "detail": str(detail)[:400]}


def _as_int(value: object) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError, OverflowError):
        return -1


def _pin_cases(page_ids: list, store_ids: list) -> dict:
    """Set EQUALITY plus a per-id count of one. Never a containment."""
    wanted = {ident for ident in store_ids if ident}
    seen = list(page_ids)
    duplicated = sorted({ident for ident in seen if seen.count(ident) > 1})
    ok = set(seen) == wanted and not duplicated
    missing = sorted(wanted - set(seen))
    extra = sorted(set(seen) - wanted)
    return _pin(
        PIN_CASES,
        ok,
        "page="
        + str(len(seen))
        + " store="
        + str(len(wanted))
        + (" missing=" + ",".join(missing[:8]) if missing else "")
        + (" unexpected=" + ",".join(extra[:8]) if extra else "")
        + (" duplicated=" + ",".join(duplicated[:8]) if duplicated else ""),
    )


def _pin_totals(summary: dict, store_tally: dict, store_count: int) -> dict:
    """Compared against a tally RECOMPUTED from the store, never the page's own."""
    problems = []
    if _as_int(summary.get("data-total")) != int(store_count):
        problems.append(
            "data-total="
            + str(summary.get("data-total"))
            + " store="
            + str(store_count)
        )
    for name, count in sorted(store_tally.items()):
        key = "data-" + str(name).replace("_", "-")
        if key not in summary:
            if int(count):
                problems.append(key + " absent but store has " + str(count))
            continue
        if _as_int(summary.get(key)) != int(count):
            problems.append(key + "=" + str(summary.get(key)) + " store=" + str(count))
    return _pin(
        PIN_TOTALS, not problems, "; ".join(problems[:8]) or "every total agrees"
    )


def _pin_card_count(cards: list, summary: dict) -> dict:
    declared = _as_int(summary.get("data-total"))
    return _pin(
        PIN_CARD_COUNT,
        len(cards) == declared,
        "cards=" + str(len(cards)) + " data-total=" + str(declared),
    )


def _pin_terminator(end: dict, cards: list) -> dict:
    if not end:
        return _pin(PIN_TERMINATOR, False, "the qa-report-end marker is absent")
    declared = _as_int(end.get("data-cards"))
    return _pin(
        PIN_TERMINATOR,
        declared == len(cards),
        "marker data-cards=" + str(declared) + " parsed cards=" + str(len(cards)),
    )


def _pin_script(page: "_Page") -> dict:
    """Exactly ONE script element, and its text IS the shell's script.

    An identity pin rather than an absence pin: the shell's behaviour (theme,
    filter, sort, deep links) is a script by necessity, and a page whose script
    differs from the constant by one byte has been interpolated into. A second
    script element, or any ``on*`` attribute, fails the same pin.
    """
    same = "".join(page.script_text) == report.SHELL_SCRIPT
    return _pin(
        PIN_NO_SCRIPT,
        page.scripts == 1 and same and not page.handlers,
        "script elements="
        + str(page.scripts)
        + (" text=shell" if same else " text=DIFFERS FROM THE SHELL")
        + " handlers="
        + ",".join(page.handlers[:6]),
    )


def _pin_links(page: "_Page", text: str) -> dict:
    """Links are font hosts, images are inline, clips point INSIDE the folder.

    Four checks, one verdict, because they answer one question: can this report
    be read by a tester who is offline, or who was emailed it? A stray ``href``,
    an image ``src`` that is a PATH rather than a payload, a media ``src`` that
    is anything but a relative sibling under the report's own media directory,
    and any of the remaining fetch constructs each break that, and each is
    reported by name.

    The media half is a SOURCE test, not a text test, and it is judged off the
    parsed ``src`` so a screen that legitimately DISPLAYS the text of a URL
    cannot redden a pin on a healthy tree.
    """
    strays = [
        href for href in page.links if not str(href).startswith(report.FONT_HOSTS)
    ]
    # IDENTITY, not absence: see the note on EXTERNAL. An image whose src is a
    # file path or an http URL is exactly the offline break this pin exists for.
    strays += [
        str(src)[:60] for src in page.images if not str(src).startswith(SHOT_SRC)
    ]
    found = [needle for needle in EXTERNAL if needle in text]
    prefix = str(report.MEDIA_DIR) + "/"
    offfolder = [
        tag + "@" + (str(src)[:60] or "(empty)")
        for tag, src in page.media
        if not str(src).startswith(prefix) or ".." in str(src)
    ]
    return _pin(
        PIN_NO_ASSET,
        not strays and not found and not offfolder,
        "links off the font hosts="
        + ",".join(strays[:4])
        + " fetch constructs="
        + ",".join(found)
        + " media outside the folder="
        + ",".join(offfolder[:4]),
    )


def _pin_absent(name: str, text: str, needles: tuple) -> dict:
    hits = sorted({needle for needle in needles if needle in text})
    return _pin(
        name, not hits, ("found " + ", ".join(hits)) if hits else "none present"
    )


def _pin_crashes(page_crashes: object, cases: object) -> dict:
    """Every crash the STORE recorded is disclosed on the page, and no other.

    An EQUALITY, per this module's own discipline: a page that dropped the
    disclosure fails, and so does one that invented a crash the store never
    recorded. The store side goes through ``crash_detector.crash_of_case``, so
    this pin cannot disagree with the renderer about where a crash lives.
    """
    from tools.mobile_evidence import crash_detector

    page = {
        str(tc): str(kind)
        for tc, kind in (page_crashes or {}).items()
        if str(kind or "")
    }
    store = {}
    for case in list(cases or []):
        crash = crash_detector.crash_of_case(case)
        if crash:
            store[str((case or {}).get("tc_id") or "")] = str(crash.get("kind") or "")
    missing = sorted(tc for tc in store if page.get(tc) != store[tc])
    invented = sorted(tc for tc in page if tc not in store)
    return _pin(
        PIN_CRASH,
        not missing and not invented,
        "store="
        + str(len(store))
        + " page="
        + str(len(page))
        + (" undisclosed=" + ",".join(missing[:8]) if missing else "")
        + (" unexpected=" + ",".join(invented[:8]) if invented else ""),
    )


def _pin_capture_tier(manifest: dict) -> dict:
    """A manifest carrying a ``capture`` block must state a tier, and that
    tier must be one of ``tools.mobile_capture.ladder.TIERS`` (T7.4).

    A manifest with NO ``capture`` key at all -- a run planned before this
    feature -- has nothing to state, so this pin is inert on it rather than a
    false failure: absence and invalidity are two different findings.
    """
    from tools.mobile_capture import ladder

    capture = manifest.get("capture") if isinstance(manifest, dict) else None
    if not isinstance(capture, dict):
        return _pin(PIN_CAPTURE_TIER, True, "no capture block on this manifest")
    tier = capture.get("tier")
    ok = tier in ladder.TIERS
    return _pin(
        PIN_CAPTURE_TIER, ok, "tier=" + repr(tier) if not ok else "tier=" + str(tier)
    )


def check(run_id: str, html_path: str = "") -> dict:
    """Every pin against the page for *run_id*. Never raises.

    With no *html_path* the report is RENDERED first, so the check runs against
    the current state of the store rather than against whatever page happened to
    be lying on disk.
    """
    try:
        target = str(html_path or "")
        if not target:
            produced = report.render(run_id)
            if produced.get("error"):
                return {"error": str(produced["error"]), "content": None}
            target = str((produced.get("content") or {}).get("path") or "")
        path = Path(target)
        if not path.is_file():
            return {"error": "No report file at " + target[:200], "content": None}
        text = path.read_text(encoding="utf-8", errors="replace")
        size = len(text.encode("utf-8", errors="replace"))
        cases = [
            case
            for case in (run_store.list_cases(run_id) or {}).get("content") or []
            if isinstance(case, dict)
        ]
        store_ids = [str(case.get("tc_id") or "") for case in cases]
        manifest = (run_store.read_manifest(run_id) or {}).get("content")
        manifest = manifest if isinstance(manifest, dict) else {}
        store_tally = report.tally(cases)
        page = _Page()
        page.feed(text)
        page.close()
        pins = [
            _pin_cases(page.cards, store_ids),
            _pin_totals(page.summary, store_tally, len(cases)),
            _pin_card_count(page.cards, page.summary),
            _pin_terminator(page.end, page.cards),
            _pin_absent(PIN_NO_MARKERS, text, FORBIDDEN_MARKERS),
            _pin_absent(PIN_NO_XML, text, RAW_XML),
            _pin_script(page),
            _pin_links(page, text),
            _pin_absent(PIN_NO_SECRET, text, SECRET_TOKENS),
            _pin(
                PIN_SIZE,
                size <= report.MAX_PAGE_BYTES,
                str(size) + " bytes of " + str(report.MAX_PAGE_BYTES),
            ),
            _pin_crashes(page.crashes, cases),
            _pin_capture_tier(manifest),
        ]
        return {
            "error": None,
            "content": {
                "ok": all(entry["ok"] for entry in pins),
                "pins": pins,
                "path": str(path),
                "bytes": size,
                "cards": len(page.cards),
            },
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.report_selfcheck.check failed")
        return {"error": str(exc), "content": None}


def main(argv: list | None = None) -> int:
    """``0`` when every pin passed, ``1`` on any failure, ``2`` on bad usage."""
    args = [str(item) for item in (sys.argv[1:] if argv is None else argv)]
    run_id = ""
    html_path = ""
    index = 0
    while index < len(args):
        item = args[index]
        if item == "--html" and index + 1 < len(args):
            html_path = args[index + 1]
            index += 2
            continue
        if not run_id and not item.startswith("-"):
            run_id = item
        index += 1
    if not run_id:
        sys.stdout.write(USAGE)
        return 2
    result = check(run_id, html_path)
    if result.get("error"):
        sys.stdout.write("REFUSED " + str(result["error"])[:400] + "\n")
        return 1
    body = result.get("content") or {}
    for entry in body.get("pins") or []:
        sys.stdout.write(
            ("PASS " if entry.get("ok") else "FAIL ")
            + str(entry.get("name"))
            + " - "
            + str(entry.get("detail"))
            + "\n"
        )
    sys.stdout.write(
        ("OK " if body.get("ok") else "FAILED ")
        + str(body.get("path"))
        + " ("
        + str(body.get("cards"))
        + " cards, "
        + str(body.get("bytes"))
        + " bytes)\n"
    )
    return 0 if body.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
