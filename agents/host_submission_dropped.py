"""Host mode: the all-cases-dropped error and the dropped-reason cap."""

from __future__ import annotations

from agents.host_prep_serde import PrepParseError

# Cap on how many dropped-case reasons parse_host_suite reports, so a hostile
# 100k-garbage-case submission cannot produce a 100k-line reason list.
_MAX_DROPPED_REASONS = 20


def _group_by_reason(dropped: list) -> dict[str, list[str]]:
    by_reason: dict[str, list[str]] = {}
    for d in dropped:
        id_, _, reason = str(d).partition(": ")
        by_reason.setdefault(reason or str(d), []).append(id_)
    return by_reason


def _reason_parts(by_reason: dict[str, list[str]]) -> list[str]:
    parts: list[str] = []
    for reason, ids in list(by_reason.items())[:_MAX_DROPPED_REASONS]:
        if len(ids) == 1:
            parts.append(f"{ids[0]}: {reason}")
        else:
            parts.append(f"{len(ids)} cases ({ids[0]}..{ids[-1]}): {reason}")
    return parts


# 2026-08-31: this raised with the COUNT alone and discarded `dropped`
# -- the per-case field/rule detail F15 had just built. The all-dropped
# case is where that detail matters MOST: every case failing at once is
# what ONE systematic schema mistake looks like, so a single reason
# usually fixes the whole suite. Without it the host was told
# "(8 dropped)" and had to re-derive the schema by guessing. Measured:
# 8 cases missing `module` and `type` reported nothing but the number.
# Collapsed by REASON, not listed per case. Every case failing at once
# is almost always ONE systematic mistake, and repeating an identical
# sentence eighty times buries the single fact that fixes the suite --
# while costing the host's context to read it.
def _all_dropped_error(dropped: list) -> PrepParseError:
    """The error raised when NO submitted case survived validation."""
    by_reason = _group_by_reason(dropped)
    why = "; ".join(_reason_parts(by_reason))
    more = len(by_reason) - _MAX_DROPPED_REASONS
    if more > 0:
        why += f" (+{more} more distinct reason(s))"
    return PrepParseError(
        f"no valid test cases in the submitted suite ({len(dropped)} "
        f"dropped). Why: {why}"
    )
