"""Labels for cases that were refused before they had an id or a title.

A refused script produced a case dict with no ``tc_id`` and no ``title``, and
the report rendered it as a bare ``?``. :func:`refusal_case` always supplies
both; :func:`label_for` is the one fallback every renderer uses.
"""

from __future__ import annotations

from typing import Optional

UNLABELLED = "unlabelled step"


def _real(value: object) -> str:
    """The text, or '' when it is blank or only a placeholder question mark."""
    text = " ".join(str(value or "").split())
    return "" if text == "?" else text


def _position(op_index: object) -> Optional[int]:
    try:
        number = int(op_index)  # type: ignore[call-overload]
    except (TypeError, ValueError, OverflowError):
        return None
    return number + 1 if number >= 0 else None


def refusal_case(
    op_index: object, reason: str, tc_id: str = "", title: str = ""
) -> dict:
    """The case dict for a refused step.

    *op_index* is the 0-based position of the refused action; None (the whole
    script was refused) gives ``SCRIPT`` / ``Refused script``.
    """
    number = _position(op_index)
    tag = "STEP-" + str(number) if number else "SCRIPT"
    name = "Refused step " + str(number) if number else "Refused script"
    return {
        "tc_id": _real(tc_id) or tag,
        "title": _real(title) or name,
        "reason": str(reason or ""),
    }


def label_for(case: object) -> str:
    """A readable label for any case dict; never empty and never a bare ``?``."""
    body = case if isinstance(case, dict) else {}
    tc_id = _real(body.get("tc_id"))
    title = _real(body.get("title"))
    if tc_id and title:
        return tc_id + " - " + title
    return tc_id or title or UNLABELLED
