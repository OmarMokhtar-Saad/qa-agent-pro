"""Offer the tester the uiautomator2 helper for one device, once.

The helper installs two small apps on the tester's device, so the choice is the
TESTER's: :func:`offer` asks through ``user_confirm.confirm`` (an elicitation
answer, never a tool argument the host model could fill) and only a genuine
``UserConsent`` reaches ``tree_optin.enable``. Nothing here takes a flag or a
consent value from a caller.

True means the tester said yes just now and the choice was saved. False covers
everything else: the helper package is not installed (nothing to offer), the
device is already opted in (nothing to ask), the tester already said no to this
device while this server runs (asked once, not on every call), no way to ask, a
no, a failing ask transport, an unusable serial, a store that could not be written. It never
raises; ``asyncio.CancelledError`` is a ``BaseException`` and still propagates.
"""

from __future__ import annotations

import importlib.util
import logging
from typing import Optional

from tools.mobile import tree_optin, user_confirm
from tools.mobile.resolve import AskCb

log = logging.getLogger(__name__)

HELPER_MODULE = "uiautomator2"

_MAX_SERIAL_SHOWN = 64
# Serials whose offer got anything but a yes; insertion-ordered, oldest dropped.
_MAX_DECLINED = 64
_declined: dict[str, None] = {}
# What a prompt or opt-in store failure can raise; anything else is a bug.
_FAILURES = (
    OSError,
    RuntimeError,
    TimeoutError,
    ValueError,
    LookupError,
    TypeError,
    AttributeError,
)


def _installed() -> bool:
    try:
        return importlib.util.find_spec(HELPER_MODULE) is not None
    except (ImportError, ValueError):
        return False


def _remember_decline(key: str) -> None:
    while len(_declined) >= _MAX_DECLINED:
        _declined.pop(next(iter(_declined)))
    _declined[key] = None


def _question(serial: str) -> str:
    return (
        "Optional: turn on faster screen reads for device "
        + str(serial or "")[:_MAX_SERIAL_SHOWN]
        + "? This installs two small helper apps on the device. Without them "
        "testing still works, just more slowly."
    )


def _recording(ask: Optional[AskCb], answers: list) -> Optional[AskCb]:
    """``ask`` wrapped so the answer it returned is kept in ``answers``."""
    if ask is None:
        return None

    async def asked(question, options):
        answer = await ask(question, options)
        answers.append(answer)
        return answer

    return asked


def _explicit_decline(answers: list) -> bool:
    """True when the tester answered with a non-blank text (never the YES
    option here: a yes would have minted the consent)."""
    last = answers[-1] if answers else None
    return isinstance(last, str) and bool(last.strip())


def _enable_or_remember(serial: str, key: str, consent: object) -> bool:
    """Save a yes; one that cannot be saved is remembered like a no (asked once)."""
    try:
        saved = bool(tree_optin.enable(serial, consent))
    except _FAILURES:
        log.warning("u2_consent: the opt-in could not be saved", exc_info=True)
        saved = False
    if not saved:
        _remember_decline(key)
    return saved


async def offer(serial: str, ask: Optional[AskCb]) -> bool:
    """Ask the tester about the helper for ``serial``; True only on a saved yes."""
    key = str(serial or "")
    if key in _declined or not _installed() or tree_optin.is_enabled(serial):
        return False
    answers: list = []
    try:
        consent = await user_confirm.confirm(
            _question(serial),
            tree_optin.action_for(serial),
            _recording(ask, answers),
        )
        if consent is None:
            if _explicit_decline(answers):
                _remember_decline(key)
            return False
        return _enable_or_remember(serial, key, consent)
    except _FAILURES:  # an offer must never break the run it rides on
        log.warning("u2_consent: the offer failed", exc_info=True)
        return False
