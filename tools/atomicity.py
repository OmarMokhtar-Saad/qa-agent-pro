"""Atomicity / anti-bundling rule pack (Batch 3 / rule pack 2).

A bundled test case verifies a backend state change AND a UI outcome at once.
It passes on the visible half while the hidden half is silently broken, so the
tester ticks it green and the defect ships. The 2025 consensus formulation is
"one BEHAVIOUR per test" (not "one assertion per test"): several assertions are
fine as long as they describe the same behaviour, and two behaviours that live
in different SUBSYSTEMS must be split.

The pack is three things:

* ``ATOMICITY_INSTRUCTION`` -- the split rule, appended to the generator system
  prompt. Deliberately says "split across SUBSYSTEMS, not across cosmetic UI
  toggles that happen in one rendering pass", so a case asserting a button
  enables and a hint appears is NOT split.
* ``detect_bundled_cases`` -- signal 1, purely textual. It reads EXPECTED
  RESULTS only (actions legitimately cross subsystems: you click a button to hit
  an API) and flags a case whose expected results assert outcomes in two or more
  distinct subsystems. Self-suppresses above ``_MAX_FLAG_RATIO``.
* ``detect_cross_line_bundles`` -- signal 2, checklist-driven. Given the
  backward matcher's ``{line_id: [tc_id, ...]}`` mapping and a
  ``{line_id: subsystem}`` map, one case satisfying lines from DIFFERENT
  subsystems is bundling. Pure dict arithmetic -- this module deliberately does
  NOT import ``tools.rtm``, so nothing new is added to that dist-visible
  module's public surface and there is no import cycle with the matcher.

FLAG ONLY. Nothing is dropped, split or rewritten -- splitting a case in code
would mean fabricating steps. Never raises.
"""

from __future__ import annotations

import logging
import re

from tools.models import TestCase

logger = logging.getLogger(__name__)

# Coarse on purpose. A finer taxonomy (forms vs. navigation vs. modals) would
# flag every realistic case; these three are the boundaries where a hidden half
# can actually be silently broken.
SUBSYSTEMS: dict[str, tuple[str, ...]] = {
    "backend": (
        r"\bAPI\b",
        r"\bendpoint\b",
        r"\brequest\b",
        r"\bresponse\b",
        r"\bpayload\b",
        r"\bstatus code\b",
        r"\bHTTP\s*[1-5]\d\d\b",
        r"\b(?:status|code|HTTP|returns?|responds? with)\s*(?:code)?\s*[:=]?\s*"
        r"(?:2\d\d|4\d\d|5\d\d)\b",
        r"\bdatabase\b",
        r"\bDB\b",
        r"\btable\b",
        r"\brecord\b",
        r"\bpersist(?:ed|s|ence)?\b",
        r"\bstored\b",
        r"\bsaved to\b",
        r"\bbackend\b",
        r"\bserver[- ]side\b",
        r"\bqueue[ds]?\b",
        r"\baudit log\b",
        r"\bledger\b",
        r"\btransaction (?:is )?(?:created|committed|rolled back)\b",
    ),
    "ui": (
        r"\bscreen\b",
        r"\bpage\b",
        r"\bbanner\b",
        r"\btoast\b",
        r"\bsnackbar\b",
        r"\bmodal\b",
        r"\bdialog\b",
        r"\bnavigat(?:e|es|ed|ion)\b",
        r"\bredirect(?:s|ed)?\b",
        r"\blands? on\b",
        r"\bdisplayed\b",
        r"\bis shown\b",
        r"\brendered\b",
        r"\bbutton\b",
        r"\bfield\b",
        r"\blabel\b",
        r"\bspinner\b",
        r"\btooltip\b",
    ),
    "notification": (
        r"\bemail\b",
        r"\bSMS\b",
        r"\bpush notification\b",
        r"\bwebhook\b",
        r"\bnotification is sent\b",
        r"\bOTP\b",
    ),
}

_COMPILED: dict[str, tuple[re.Pattern, ...]] = {
    name: tuple(re.compile(p, re.IGNORECASE) for p in pats)
    for name, pats in SUBSYSTEMS.items()
}

# If the detector would flag more than this share of the suite it is reacting to
# generic vocabulary, not to real bundling, so it suppresses itself rather than
# handing the tester a useless wall of ids. Mirrors tools/ac_anchor.py.
_MAX_FLAG_RATIO = 0.5

ATOMICITY_INSTRUCTION = """

## ATOMICITY RULE (mandatory)
- One test case verifies ONE behaviour. Several expected results are fine when
  they describe the same behaviour.
- NEVER bundle a data / state / backend outcome with a UI or navigation outcome
  in the same test case. "The order is written to the orders table AND the
  confirmation page opens" is TWO test cases: one that verifies the persisted
  record, one that verifies the screen. The bundled version passes on the
  visible half while the hidden half is silently broken.
- The split boundary is the SUBSYSTEM (backend/data, UI, notification), NOT
  cosmetic UI changes: a button enabling, a hint appearing and a field clearing
  all happen in one rendering pass and belong in ONE case.
- When you split, each resulting case must still be independently executable -
  restate the setup steps it needs instead of relying on the other half.
"""


def classify_subsystems(text: str) -> set[str]:
    """Subsystem names whose vocabulary appears in *text*. Never raises."""
    found: set[str] = set()
    try:
        blob = text or ""
        for name, patterns in _COMPILED.items():
            for pat in patterns:
                if pat.search(blob):
                    found.add(name)
                    break
    except Exception:  # pragma: no cover - defensive
        logger.exception("classify_subsystems failed - returning nothing")
        return set()
    return found


def _expected_text(tc: TestCase) -> str:
    chunks: list[str] = []
    for step in getattr(tc, "steps", None) or []:
        chunks.append(getattr(step, "expected_result", "") or "")
    chunks.append(getattr(tc, "postconditions", "") or "")
    return "\n".join(c for c in chunks if c)


def detect_bundled_cases(cases: list[TestCase]) -> dict[str, list[str]]:
    """Map tc_id -> sorted subsystem names for every case that bundles.

    Only EXPECTED RESULTS (plus postconditions) are scanned: a step's *action*
    crossing a subsystem boundary is normal ("click Pay" -> hits an API), while
    an *assertion* crossing one is the bundling defect.

    Returns {} when nothing bundles, or when more than half the suite would be
    flagged (vocabulary mismatch, not real bundling). Never raises.
    """
    try:
        if not cases:
            return {}
        flagged: dict[str, list[str]] = {}
        for tc in cases:
            subsystems = classify_subsystems(_expected_text(tc))
            if len(subsystems) >= 2:
                flagged[tc.tc_id] = sorted(subsystems)
        if flagged and len(flagged) > len(cases) * _MAX_FLAG_RATIO:
            logger.info(
                "Atomicity detector flagged %d/%d cases - suppressing as a "
                "vocabulary mismatch rather than real bundling",
                len(flagged),
                len(cases),
            )
            return {}
        return flagged
    except Exception:
        logger.exception("detect_bundled_cases failed - flagging nothing")
        return {}


def cases_covering_multiple(
    matches: dict[str, list[str]], min_lines: int = 2
) -> dict[str, list[str]]:
    """Invert ``{line_id: [tc_id, ...]}`` to the cases that cover >= min_lines.

    Pure dict arithmetic on the BACKWARD matcher's output, kept HERE rather than
    added to ``tools/rtm.py`` so Batch 3 adds no new public function to that
    dist-visible module. Never raises.
    """
    try:
        by_case: dict[str, list[str]] = {}
        for line_id, tc_ids in (matches or {}).items():
            for tc_id in tc_ids or []:
                by_case.setdefault(str(tc_id), []).append(str(line_id))
        floor = max(2, int(min_lines or 2))
        return {
            tc_id: sorted(set(line_ids))
            for tc_id, line_ids in by_case.items()
            if len(set(line_ids)) >= floor
        }
    except Exception:
        logger.exception("cases_covering_multiple failed - returning nothing")
        return {}


def detect_cross_line_bundles(
    matches: dict[str, list[str]], line_subsystems: dict[str, str]
) -> dict[str, list[str]]:
    """Checklist-driven bundling signal.

    ``matches`` is the BACKWARD matcher's output -- ``{checklist_line_id:
    [tc_id, ...]}``, produced from a Batch-2 ``ChecklistCoverage`` by
    ``tools.rule_packs.coverage_matches`` -- and ``line_subsystems`` maps each
    line id to its subsystem. A case satisfying two or more lines from DIFFERENT
    subsystems is bundling. Returns ``{tc_id: [line_id, ...]}``, or {} when
    either input is empty. Never raises.
    """
    try:
        if not matches or not line_subsystems:
            return {}
        by_case = cases_covering_multiple(matches, min_lines=2)
        flagged: dict[str, list[str]] = {}
        for tc_id, line_ids in by_case.items():
            subs = {line_subsystems.get(lid, "") for lid in line_ids}
            subs.discard("")
            if len(subs) >= 2:
                flagged[tc_id] = sorted(line_ids)
        return flagged
    except Exception:
        logger.exception("detect_cross_line_bundles failed - flagging nothing")
        return {}


def bundling_warning_section(
    bundled: dict[str, list[str]], cross_line: dict[str, list[str]] | None = None
) -> str:
    """Advisory markdown naming bundled cases. "" when nothing was flagged.

    Never raises.
    """
    try:
        cross_line = cross_line or {}
        if not bundled and not cross_line:
            return ""
        lines = [
            "\n\n## Atomicity Check (advisory)",
            "",
            "These test cases assert outcomes in more than one subsystem. A bundled "
            "case passes on the visible half while the hidden half is silently "
            "broken - split each one so the backend outcome and the UI outcome are "
            "verified separately. Nothing was removed:",
        ]
        for tc_id in sorted(bundled)[:20]:
            lines.append(f"- **{tc_id}** - asserts: {', '.join(bundled[tc_id])}")
        if len(bundled) > 20:
            lines.append(f"- ... and {len(bundled) - 20} more")
        extra = [tc_id for tc_id in sorted(cross_line) if tc_id not in bundled]
        for tc_id in extra[:20]:
            lines.append(
                f"- **{tc_id}** - one case covering checklist requirements from "
                f"different subsystems: {', '.join(cross_line[tc_id])}"
            )
        if len(extra) > 20:
            lines.append(f"- ... and {len(extra) - 20} more (checklist signal)")
        return "\n".join(lines)
    except Exception:
        logger.exception("bundling_warning_section failed - returning ''")
        return ""
