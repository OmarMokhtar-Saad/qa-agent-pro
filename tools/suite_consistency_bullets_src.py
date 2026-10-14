"""The bullets built from source-grounded findings: UI strings, bounds, classes."""

from __future__ import annotations

from collections import defaultdict

from tools import suite_consistency_limits as _lim


def _amount(value: float) -> str:
    """Thousands-separated, and without a trailing '.0' on a whole number."""
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,}"


def _ungrounded_headline(count: int) -> str:
    return (
        f"- **{count} exact UI string(s) are asserted that "
        "the source never promises** — the tester compares "
        "the product against copy nobody wrote, so any wording "
        "difference reads as a defect. Assert what the message "
        'MUST SAY ("an error that names the field and the limit it '
        'broke") instead '
        "of its exact text, or get the real copy into the ticket:"
    )


def _ungrounded_row(span: str, ids: list[str]) -> str:
    shown = span if len(span) <= 60 else span[:57] + "..."
    where = ids[0] if len(ids) == 1 else f"{ids[0]} +{len(ids) - 1} more"
    return f"  - '{shown}' ({where})"


# PROTECTED. This bullet says the case asserts copy the product never
# promised -- a false oracle a tester files a bug against. It is also
# the grounding/trust signal, and the bullet whose silent absence
# made the original bound vacuous; it must never be the one dropped.
# A FOURTH bullet in this block rather than a heading of its
# own, for the reason recorded above the conditional-action
# bullet: this is the same class of advice -- "this assertion
# cannot do its job" -- and a second heading costs finalize-
# reply budget that F03 already measured as scarce.
#
# Grouped by the invented STRING, not by tc_id like the two
# bullets above, because the string IS the defect: suite
# b8b8ed00 repeats one invented 'Transfer scheduled' across
# all 96 of its cases, and grouping by case would spend twenty
# lines of the reply saying the same thing twenty times.
def _add_ungrounded_bullet(
    lines: list[str], order: list[tuple[str, int]], ungrounded
) -> None:
    order.append(("invented UI strings", 0))
    by_span: dict[str, set[str]] = defaultdict(set)
    for tc_id, _step_number, span in ungrounded:
        by_span[span].add(tc_id)
    lines.append(_ungrounded_headline(len(by_span)))
    for span in sorted(by_span)[: _lim._MAX_REPORTED]:
        lines.append(_ungrounded_row(span, sorted(by_span[span])))
    if len(by_span) > _lim._MAX_REPORTED:
        lines.append(f"  - ... and {len(by_span) - _lim._MAX_REPORTED} more")


def _bounds_headline(count: int) -> str:
    return (
        f"- **{count} case(s) contradict a numeric bound the "
        "source states** — the value the step enters is legal under "
        "the range the source gives for that field yet the step asserts "
        "it is rejected (or the reverse), so the case fails against "
        "correct software. Check the value, and check it is on the "
        "field the range actually belongs to:"
    )


def _bounds_row(finding) -> str:
    side = "inside" if finding.kind == "legal-rejected" else "outside"
    verdict = (
        "the step expects it to be rejected"
        if finding.kind == "legal-rejected"
        else "the step expects it to be accepted"
    )
    return (
        f"  - {finding.tc_id} (step {finding.step_number}): "
        f"{_amount(finding.value)} is {side} the "
        f"{_amount(finding.low)}–{_amount(finding.high)} the source "
        f"states for the {finding.subject}, but {verdict}"
    )


# PROTECTED, same class as the bullet above: the case asserts that a
# documented-legal value is rejected, so it fails against correct
# software. Wrong, not thin.
# A FIFTH bullet, same block, for the reason recorded above the
# conditional-action bullet: same class of advice ("this case
# cannot pass against correct software"), and a heading of its own
# costs finalize-reply budget F03 already measured as scarce.
def _add_bounds_bullet(lines: list[str], order: list[tuple[str, int]], bounds) -> None:
    order.append(("bound contradictions", 0))
    lines.append(_bounds_headline(len(bounds)))
    for finding in bounds[: _lim._MAX_REPORTED]:
        lines.append(_bounds_row(finding))
    if len(bounds) > _lim._MAX_REPORTED:
        lines.append(f"  - ... and {len(bounds) - _lim._MAX_REPORTED} more")


def _classes_headline(count: int) -> str:
    return (
        f"- **{count} test class(es) an experienced "
        "tester would expect are missing or thin.** Each line "
        "gives how many cases actually exercise the class "
        "against how many merely mention its subject:"
    )


def _classes_row(finding) -> str:
    got = len(finding.qualifying)
    seen = len(finding.subject_hits)
    tail = (
        " - the source does not specify the outcome, so "
        "cover it as an exploratory charter that RECORDS "
        "the behaviour, never as an asserted result"
        if finding.unspecified
        else ""
    )
    return (
        f"  - {finding.label}: {got} of {finding.floor} "
        f"case(s), from {seen} that mention the subject{tail}"
    )


# A SIXTH bullet, same block, same reasoning as the ones
# above: same class of advice, and a heading of its own
# costs finalize-reply budget F03 measured as scarce.
#
# Each line reports QUALIFYING against MENTIONED, because
# the two numbers say different things and only the pair
# is actionable: '3 mention, 0 test it' means the subject
# is covered on the happy path and untested on the failure
# path, which is the single most common shape of this gap.
def _add_classes_bullet(
    lines: list[str], order: list[tuple[str, int]], classes
) -> None:
    order.append(("missing coverage classes", _lim._ADV_P_SUGGESTION))
    lines.append(_classes_headline(len(classes)))
    for finding in classes[: _lim._MAX_REPORTED]:
        lines.append(_classes_row(finding))
    if len(classes) > _lim._MAX_REPORTED:
        lines.append(f"  - ... and {len(classes) - _lim._MAX_REPORTED} more")
