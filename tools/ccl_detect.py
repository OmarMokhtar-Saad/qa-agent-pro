"""Per-class detection for the coverage-class detector."""

from __future__ import annotations

from tools.ccl_types import ClassFinding, CoverageClass


def _case_texts(tc: object) -> tuple[str, str]:
    """Return ``(full_text, body_text)``.

    ``body_text`` deliberately EXCLUDES the title: adversity is matched against
    it, so an aspirational title alone can never qualify a class.
    """
    title = str(getattr(tc, "title", "") or "")
    parts: list[str] = []
    for step in getattr(tc, "steps", None) or []:
        action = getattr(step, "action", None)
        expected = getattr(step, "expected_result", None)
        if action is None and isinstance(step, dict):
            action = step.get("action")
            expected = step.get("expected_result")
        parts.append(str(action or ""))
        parts.append(str(expected or ""))
    parts.append(str(getattr(tc, "preconditions", "") or ""))
    body = "\n".join(parts)
    return (title + "\n" + body), body


def prepare_cases(cases: list) -> list[tuple[str, str, str]]:
    prepared: list[tuple[str, str, str]] = []
    for tc in cases or []:
        tc_id = str(getattr(tc, "tc_id", "") or "")
        full, body = _case_texts(tc)
        prepared.append((tc_id, full, body))
    return prepared


# A class with ZERO mentions is not reported, and this is the
# scope gate the whole detector rests on. Nothing here knows what
# feature the suite is about, so without it a cancel-order suite
# is told it is missing currency conversion and pre-auth holds --
# nine fixed bullets on every suite ever generated, which both
# breaks the '' clean-suite contract of the section above and
# spends finalize-reply budget on advice that is content-free:
# "0 of 1 case(s), from 0 that mention the subject" repeats the
# class list back at the tester.
#
# The cost is stated rather than hidden: on the calibration suite
# this reports 7 of the 9 hand-read gaps, not 9. The two dropped
# (refunds/reversals, pre-authorisation holds) are exactly the
# ones whose subject appears in NEITHER the suite nor its source,
# so there is no evidence they are in scope -- and the go-ahead
# already routes those to an exploratory charter or an ambiguity
# flag rather than to a generation nudge.
def missing_finding(
    spec: CoverageClass, prepared: list[tuple[str, str, str]]
) -> ClassFinding | None:
    """The class's finding when mentioned but under its floor, else None."""
    finding = ClassFinding(
        class_id=spec.class_id,
        label=spec.label,
        floor=spec.floor,
        unspecified=spec.unspecified,
    )
    for tc_id, full, body in prepared:
        if not spec.subject.search(full):
            continue
        finding.subject_hits.append(tc_id)
        if spec.adversity is None or spec.adversity.search(body):
            finding.qualifying.append(tc_id)
    if finding.subject_hits and len(finding.qualifying) < spec.floor:
        return finding
    return None
