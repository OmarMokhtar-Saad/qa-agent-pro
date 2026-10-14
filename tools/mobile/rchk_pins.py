"""The completeness and identity pins of the report self-check."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tools.mobile import report
from tools.mobile.rchk_consts import (
    PIN_CARD_COUNT,
    PIN_CASES,
    PIN_NO_SCRIPT,
    PIN_TERMINATOR,
    PIN_TOTALS,
)

if TYPE_CHECKING:
    from tools.mobile.rchk_page import _Page


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


def _total_problems(summary: dict, store_tally: dict, store_count: int) -> list:
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
    return problems


# Compared against a tally RECOMPUTED from the store, never the page's own.
def _pin_totals(summary: dict, store_tally: dict, store_count: int) -> dict:
    """The page's ``data-*`` totals against the store's tally."""
    problems = _total_problems(summary, store_tally, store_count)
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


def _pin_absent(name: str, text: str, needles: tuple) -> dict:
    hits = sorted({needle for needle in needles if needle in text})
    return _pin(
        name, not hits, ("found " + ", ".join(hits)) if hits else "none present"
    )
