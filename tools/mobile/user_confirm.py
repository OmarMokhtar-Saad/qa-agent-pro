"""User-only confirmation for actions the host model must not approve itself.

Uninstalling an app (signing mismatch on update) or installing the uiautomator2
helper APKs changes the tester's device. A tool argument such as
``confirm_destructive=true`` is written by the host model, so it is not consent.
Consent here is an answer the TESTER gave through MCP elicitation: the injected
``ask`` callback (the host builds it from ``_elicit_choice``) returns the option
the tester picked, and only the exact yes option counts.

Fails closed: no callback, a decline, a timeout, an unexpected answer or a
raising transport all mean "no".
"""

from __future__ import annotations

import logging
from typing import Optional

from tools.mobile.resolve import ASK_FAILURES, AskCb

log = logging.getLogger(__name__)

YES = "Yes, go ahead"
NO = "No, stop"

_MINT = object()


class UserConsent:
    """Proof that the tester said yes to one action. Only :func:`confirm`
    can create it; constructing one directly raises TypeError."""

    __slots__ = ("action",)

    def __init__(self, action: str, *, _token: object = None) -> None:
        if _token is not _MINT:
            raise TypeError("UserConsent is minted only by user_confirm.confirm")
        self.action = action

    def __repr__(self) -> str:
        return "UserConsent(" + repr(self.action) + ")"


async def confirm(
    question: str, action: str, ask: Optional[AskCb]
) -> Optional[UserConsent]:
    """Ask the tester ``question``; a :class:`UserConsent` for ``action`` only
    when they picked :data:`YES`, otherwise None."""
    if ask is None:
        return None
    try:
        answer = await ask(question, [YES, NO])
    except ASK_FAILURES:  # a transport that cannot ask means no consent
        log.warning("user_confirm: ask callback failed", exc_info=True)
        return None
    if answer != YES:
        return None
    return UserConsent(action, _token=_MINT)
