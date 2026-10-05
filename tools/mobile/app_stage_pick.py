"""Resolve-or-ask for the app text the mobile app stage receives.

The stage used to take free text such as ``acme app qa`` and guess a package
from it. This asks ``app_pick.pick_package`` over what is installed on THIS
device instead:

* one match -> the package, plus a sentence naming it (``said``);
* several (qa / master / prod ...) -> a ``wrap_untrusted`` menu for the tester
  (the tester was already asked by dialog when the client can show one);
* anything else -> an EMPTY outcome, and the caller keeps its old behaviour
  (its own "not installed" text with suggestions).

Empty is also the answer when there is nothing to resolve: no serial, no text,
or text that already is a package id. Nothing here guesses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from tools.device_manager import valid_package_name
from tools.mobile import app_pick
from tools.mobile.app_pick import PickRequest
from tools.mobile.resolve import ASK, AskCb, Resolution
from tools.untrusted import wrap_untrusted

log = logging.getLogger(__name__)

#: What a failing device listing may raise. Anything else is a bug and propagates.
LIST_FAILURES = (OSError, RuntimeError, TimeoutError, ValueError)

Picker = Callable[..., Awaitable[Resolution]]


@dataclass(frozen=True)
class PickOutcome:
    """``package`` set: use it. ``menu`` set: return it to the tester. Neither:
    nothing was resolved here, take the old path."""

    package: str = ""
    menu: str = ""
    said: str = ""


async def pick_installed(
    serial: str,
    text: str,
    ask: Optional[AskCb],
    *,
    picker: Optional[Picker] = None,
) -> PickOutcome:
    """Resolve ``text`` against the packages installed on ``serial``."""
    wanted = " ".join(str(text or "").split())
    if not str(serial or "").strip() or not wanted or valid_package_name(wanted):
        return PickOutcome()
    run = picker or app_pick.pick_package
    try:
        got = await run(PickRequest(wanted, str(serial)), ask=ask)
    except LIST_FAILURES:
        log.warning("app_stage_pick: the device listing failed", exc_info=True)
        return PickOutcome()
    if got.resolved:
        return PickOutcome(package=str(got.value), said=got.said())
    if got.status == ASK and got.options:
        return PickOutcome(menu=wrap_untrusted("installed apps", got.menu()))
    return PickOutcome()
