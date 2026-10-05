"""Tester questions for the mobile lane: one timeout ends the asking.

The host builds an elicitation callback (``mcp_handlers._elicit_choice`` over the
client's chooser). A mobile run may ask several questions, and a client that
cannot show dialogs would make each one wait out its own timeout. After ONE
timed-out question for a client, every later question for that client returns
``None`` at once, so the caller shows its text menu instead of paying the wait
again. ``mcp_handlers._ELICIT_GATE_STRIKES`` (two strikes, process wide) is NOT
changed: this is a stricter rule that only the mobile lane uses.

This module imports nothing from ``tools.mcp_handlers``: the host injects the
elicit function and the client key.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from tools.mobile.resolve import AskCb

log = logging.getLogger(__name__)

#: Clients remembered as timed out; the oldest is dropped first. A process
#: serves a handful of clients, so this is a runaway guard, not a working set.
MAX_TIMED_OUT_CLIENTS = 32

#: ``ChoiceResult.status`` of an answered dialog (the host's ``CHOSEN``).
STATUS_CHOSEN = "chosen"

ElicitFn = Callable[[Any, str, list], Awaitable[Any]]

_TIMED_OUT: dict = {}


def reset() -> None:
    """Forget every timed-out client (tests, and a client that reconnects)."""
    _TIMED_OUT.clear()


def timed_out(key: str) -> bool:
    return key in _TIMED_OUT


def _remember_timeout(key: str) -> None:
    _TIMED_OUT.pop(key, None)
    _TIMED_OUT[key] = True
    while len(_TIMED_OUT) > MAX_TIMED_OUT_CLIENTS:
        del _TIMED_OUT[next(iter(_TIMED_OUT))]


def make_mobile_ask(
    choose: Any, elicit_choice: ElicitFn, *, client_key: Callable[[], str]
) -> Optional[AskCb]:
    """An AskCb over the host chooser, or None when the client cannot elicit.

    The callback returns the option text the tester picked, or None for a
    decline, a timeout, an unavailable client or a value that is not one of the
    offered options (so a stray answer can never read as a choice).
    """
    if choose is None:
        return None

    async def ask(question: str, options: list) -> Optional[str]:
        key = str(client_key() or "")
        if timed_out(key):
            return None
        result = await elicit_choice(choose, question, list(options))
        if getattr(result, "timed_out", False):
            _remember_timeout(key)
            log.info("mobile_elicit: a question timed out; later ones use the menu")
            return None
        if getattr(result, "status", "") != STATUS_CHOSEN:
            return None
        value = getattr(result, "value", None)
        return value if value in options else None

    return ask
