"""One slot of the submit reply and the drop order (moved from handlers._reply)."""

from __future__ import annotations

from typing import NamedTuple

from config.settings_guard import never_raise
from handlers.reply_consts import _REPLY_P_REPORT


class ReplySection(NamedTuple):
    """One slot of the submit reply. *text* carries its own separators.

    `protected` is not a priority: a protected section is never dropped and
    never truncated, whatever the budget says.
    """

    name: str
    text: str
    priority: int = _REPLY_P_REPORT
    protected: bool = False


def _omission_marker(names: list[str]) -> str:
    """Disclose what the budget left out, BY NAME.

    The wording is deliberately narrow. An earlier draft said "the Excel file
    and the Suite ID cover every case", which is false for three of the notes
    this reply can carry -- the duplicate note reports cases the server removed,
    and neither it nor the dropped-case note nor the image warning is written
    into the workbook. Only sections that genuinely repeat something the tester
    can still reach are droppable, and this text claims exactly that and no
    more.
    """
    return (
        "\n\n> \u2139\ufe0f  "
        f"{len(names)} informational section(s) were left out to keep this "
        f"reply readable: {', '.join(names)}. Every notice about whether this "
        "suite is VALID is still above -- what was left out repeats something "
        "you can still reach (the export, the coverage notes, or a tool you "
        "can call on demand).\n"
    )

    # STRICT order: cheapest-to-lose first, and every one of them goes until
    # the reply fits. No greedy back-fill -- see the docstring.
    #
    # The reserve is what the marker would cost IF THE LOOP STOPPED HERE, so
    # it is recomputed from the names dropped so far. Reserving for every
    # trimmable name up front (the round-3 review, MINOR 4) over-charged a
    # borderline reply and could drop one more section than it needed to --
    # the tester paying a section for a disclosure that was never emitted.


def _sections_to_drop(sections: list[ReplySection], used: int, cap: int) -> list[int]:
    """Indexes of the trimmable sections to drop, cheapest-to-lose first."""
    trimmable = [(i, s) for i, s in enumerate(sections) if not s.protected and s.text]
    dropped: list[int] = []
    for idx, sec in sorted(trimmable, key=lambda pair: (pair[1].priority, pair[0])):
        reserve = (
            len(_omission_marker([sections[i].name for i in dropped])) if dropped else 0
        )
        if used + reserve <= cap:
            break
        used -= len(sec.text)
        dropped.append(idx)
    return dropped if _drop_shortens(sections, dropped) else []


def _drop_shortens(sections: list[ReplySection], dropped: list[int]) -> bool:
    """A drop is only worth it when the sections outweigh the marker naming them."""
    if not dropped:
        return True
    marker = _omission_marker([sections[i].name for i in dropped])
    return sum(len(sections[i].text) for i in dropped) > len(marker)


def _unbounded_reply(sections: list[ReplySection]) -> str:
    """Fail-open join: tolerates malformed sections, never raises."""
    parts: list[str] = []
    for sec in sections or []:
        with never_raise() as g:
            parts.append(str(getattr(sec, "text", "") or ""))
        if g.failed:
            continue
    return "".join(parts)
