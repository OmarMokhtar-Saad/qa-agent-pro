"""THE freshness rule for the fast a11y tree: a repeat across an actuation is proven or dropped.

Measured: run ``mrun-20261016-173857-8f3169`` served 71 qa-ime a11y dumps
that all returned the intro screen (``7d3b14b06434``) while the video showed
every tap landing. Each consumer (the dialog answerer, ``tap_text``, the
settle's ``no_change``) trusted the tree it was handed, so each was patched
in turn and the family came back. The fix is at the source, once.

The rule: an a11y tree that is node-for-node the SAME as the previous a11y
tree, with a device command that may have moved the screen in between (the
:mod:`tools.mobile.dump_reuse` generation), is not trusted. uiautomator reads
the screen and :func:`tools.mobile.a11y_parity.compare` judges the two:

* they agree -- the screen truly did not change; the a11y tree is served.
* they disagree -- the a11y service is STALE. Fast reading is SUSPENDED until
  the tree changes (another digest: a new screen, package or window), this dump
  refuses so ``adb.uiautomator_dump`` reads the real screen itself, and the next
  changed tree is served only after one more uiautomator comparison agrees.
  :data:`MAX_STALE_STRIKES` stale verdicts in a run drop the provider for it.
* uiautomator fails -- nothing proves the repeat, so the provider refuses this
  dump and uiautomator is asked once more; the provider stays registered.

The check costs one uiautomator dump only when a tree repeats across an
actuation, which is a "tap did nothing" or the first poll of a wait: a
working fast path pays nothing.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Awaitable, Callable

from tools.mobile import adb, dump_reuse, step_timing
from tools.mobile.a11y_parity import compare, node_digest

logger = logging.getLogger("tools.mobile.a11y")

#: The ledger note and refusal for a fast tree uiautomator contradicted.
STALE_NOTE = "a11y_stale"
STALE_REFUSAL = "the a11y tree repeated across an action and uiautomator disagrees"
#: The ledger note and refusal for a repeat that could not be checked.
UNCHECKED_NOTE = "a11y_repeat_unchecked"
UNCHECKED_REFUSAL = "the a11y tree repeated across an action and could not be checked"
#: The ledger note for a repeat uiautomator confirmed.
CONFIRMED_NOTE = "a11y_repeat_confirmed"

#: serial -> (generation after the last a11y dump, its node digest).
_LAST: dict[str, tuple[int, str]] = {}
#: serial -> digest of the tree uiautomator contradicted; fast reading waits for another.
_SUSPENDED: dict[str, str] = {}
#: serial -> stale verdicts this run.
_STRIKES: dict[str, int] = {}

#: Stale verdicts in one run before fast reading stays off for the rest of it.
#: One is a transition lag (a tree read just before the screen moved); two can be
#: two slow screens; a third means the service is persistently wrong and every
#: further screen would cost a confirming dump for nothing.
MAX_STALE_STRIKES = 3

#: The refusal and ledger note while fast reading waits for the screen to change.
SUSPENDED_NOTE = "a11y_suspended"
SUSPENDED_REFUSAL = "the a11y tree is suspended until the screen changes"

Provider = Callable[[str], Awaitable[dict]]


def forget(serial: str) -> None:
    """Drop the remembered tree and the suspension (a fresh enable, a restore)."""
    for table in (_LAST, _SUSPENDED, _STRIKES):
        table.pop(str(serial), None)


def _digest(xml: str) -> str:
    """The node digest, or the raw hash when the tree cannot be pruned."""
    return node_digest(xml) or hashlib.sha1(xml.encode("utf-8", "replace")).hexdigest()


def _repeats(serial: str, digest: str, started_under: int) -> bool:
    """True when *digest* equals the last a11y tree with an actuation in between."""
    last = _LAST.get(serial)
    return last is not None and last[1] == digest and last[0] != started_under


def _stale(serial: str, digest: str) -> dict:
    """Suspend fast reading on *digest*; after :data:`MAX_STALE_STRIKES` drop it."""
    step_timing.note(STALE_NOTE)
    _STRIKES[serial] = _STRIKES.get(serial, 0) + 1
    _LAST.pop(serial, None)
    _SUSPENDED[serial] = digest
    if _STRIKES[serial] >= MAX_STALE_STRIKES:
        logger.warning("mobile.a11y: stale tree on %s; fast reading off", serial)
        adb.clear_dump_provider(serial)
    else:
        logger.warning("mobile.a11y: stale tree on %s; suspended", serial)
    return {"error": STALE_REFUSAL, "content": None}


async def _judge(serial: str, xml: str) -> dict | None:
    """``None`` when uiautomator confirms *xml*, else the refusal to return."""
    slow = await adb.uiautomator_dump(serial, use_provider=False)
    if slow.get("error"):
        step_timing.note(UNCHECKED_NOTE)
        return {"error": UNCHECKED_REFUSAL, "content": None}
    if compare(xml, str(slow.get("content") or "")).get("error"):
        return _stale(serial, _digest(xml))
    step_timing.note(CONFIRMED_NOTE)
    return None


async def _resume(serial: str, xml: str) -> dict | None:
    """While suspended: refuse the stale tree; once it moves, one confirmation."""
    if _digest(xml) == _SUSPENDED[serial]:
        step_timing.note(SUSPENDED_NOTE)
        return {"error": SUSPENDED_REFUSAL, "content": None}
    refused = await _judge(serial, xml)
    if refused is None:
        _SUSPENDED.pop(serial, None)
    return refused


async def checked(serial: str, inner: Provider) -> dict:
    """*inner*'s dump, refused when it repeats across an actuation and is not confirmed."""
    key = str(serial)
    started_under = dump_reuse.token(key)
    got = await inner(key)
    xml = str(got.get("content") or "")
    if got.get("error") or not xml:
        return got
    digest = _digest(xml)
    if key in _SUSPENDED:
        refused = await _resume(key, xml)
    elif _repeats(key, digest, started_under):
        refused = await _judge(key, xml)
    else:
        refused = None
    if refused is not None:
        return refused
    _LAST[key] = (dump_reuse.token(key), digest)
    return got


def guarded(inner: Provider) -> Provider:
    """*inner* behind :func:`checked`, the shape ``adb.set_dump_provider`` takes."""

    async def provider(serial: str) -> Any:
        return await checked(serial, inner)

    return provider
