"""Names the report self-check pins with, and the needles it looks for.

Constants only (split from ``report_selfcheck``, which re-exports every name).
"""

from __future__ import annotations

from tools.mobile import report

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
#: an image on this page is a path into the run folder's own ``shots/``
#: directory, one level UP from ``report/``, judged for IDENTITY against
#: :data:`SHOT_SRC` below. A clip sits INSIDE ``report/media/`` and may not
#: climb at all; a screen sits beside ``report/`` and must climb exactly once.
#: Two artifacts, two portability rules, each named -- rather than one rule
#: loose enough to admit both, which would admit a great deal else.
MEDIA_TAGS = ("video", "source")

#: The one ``src`` prefix an image on this page may carry, and the only climb it
#: may make. Anything else is a FETCH or an escape: an absolute path, an http
#: URL, or a second ``..`` that leaves the run directory entirely.
#:
#: This used to be a ``data:`` prefix and the rule used to be the opposite one.
#: The value moved and this guard did not need editing for the IDENTITY half,
#: because it binds the emitter's object rather than restating a literal.
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
