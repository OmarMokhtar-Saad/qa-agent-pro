"""THE freshness rule for the fast a11y tree: a repeat across an actuation is proven or dropped.

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

A repeat the event clock proves unchanged (:mod:`tools.mobile.a11y_clock`) skips it.
Before that dump, :mod:`tools.mobile.a11y_early` waits for the next event and re-reads.
"""

from __future__ import annotations

import functools
import hashlib
import logging
from typing import Any, Awaitable, Callable

from tools.mobile import (
    a11y_bench,
    a11y_clock,
    a11y_dump,
    a11y_early,
    a11y_reread,
    a11y_scrolled,
    a11y_strikes,
    dump_reuse,
    step_timing,
)
from tools.mobile.a11y_confirm import uiautomator_aside
from tools.mobile.a11y_parity import compare, node_digest
from tools.mobile.a11y_pkgs import is_ime

logger = logging.getLogger("tools.mobile.a11y")

CONFIRM_SOURCE = "uia-fresh-confirm"  # the dump-latency source of the confirmation
# Ledger notes and refusals: a contradicted fast tree, an unchecked repeat, a confirmed one.
STALE_NOTE = "a11y_stale"
STALE_REFUSAL = "the a11y tree repeated across an action and uiautomator disagrees"
UNCHECKED_NOTE = "a11y_repeat_unchecked"
UNCHECKED_REFUSAL = "the a11y tree repeated across an action and could not be checked"
CONFIRMED_NOTE = "a11y_repeat_confirmed"

#: serial -> (generation after the last a11y dump, its node digest).
_LAST: dict[str, tuple[int, str]] = {}
#: serial -> digest of the tree uiautomator contradicted; fast reading waits for another.
_SUSPENDED: dict[str, str] = {}
#: serial -> stale verdicts not yet paid back (see :mod:`tools.mobile.a11y_strikes`).
_STRIKES = a11y_strikes.STRIKES
#: serial -> packages of the tree uiautomator contradicted (another screen is not it).
_PKGS: dict[str, frozenset] = {}

MAX_STALE_STRIKES = a11y_strikes.MAX_STALE_STRIKES

SUSPENDED_NOTE = "a11y_suspended"
SUSPENDED_REFUSAL = "the a11y tree is suspended until the screen changes"

Provider = Callable[[str], Awaitable[dict]]
Confirm = Callable[[], Awaitable[Any]]


def forget(serial: str) -> None:
    """Drop the remembered tree and the suspension (a fresh enable, a restore)."""
    for table in (_LAST, _SUSPENDED, _PKGS):
        table.pop(str(serial), None)
    a11y_strikes.reset(str(serial))
    a11y_bench.forget(serial)
    a11y_clock.forget(serial)
    a11y_scrolled.forget(serial)


def _digest(xml: str) -> str:
    """The node digest, or the raw hash when the tree cannot be pruned."""
    return node_digest(xml) or hashlib.sha1(xml.encode("utf-8", "replace")).hexdigest()


_packages = a11y_early.packages  # which apps own a tree


def _other_screen(serial: str, xml: str) -> bool:
    """True when *xml* belongs to other apps than the tree judged stale.

    A keyboard window is shown over any screen, so its package is ignored on
    both sides: it appearing or going is not another screen.
    """
    now = {p for p in _packages(xml) if not is_ime(p)}
    was = {p for p in _PKGS.get(serial, frozenset()) if not is_ime(p)}
    return bool(now and was and now != was)


def _repeats(serial: str, digest: str, started_under: int) -> bool:
    """True when *digest* equals the last a11y tree with an actuation in between."""
    last = _LAST.get(serial)
    return last is not None and last[1] == digest and last[0] != started_under


def _stale(serial: str, digest: str, moved: bool) -> dict:
    """Suspend fast reading on *digest*; a strike only when events moved (B09)."""
    step_timing.note(STALE_NOTE)
    _LAST.pop(serial, None)
    a11y_clock.forget(serial)
    _SUSPENDED[serial] = digest
    if moved and a11y_strikes.add(serial) >= MAX_STALE_STRIKES:
        logger.warning("mobile.a11y: stale tree on %s; fast reading benched", serial)
        a11y_bench.bench(serial, a11y_dump.fetch_dump)
    else:
        logger.warning("mobile.a11y: stale tree on %s; suspended", serial)
    return {"error": STALE_REFUSAL, "content": None}


async def _judge(serial: str, xml: str, inner: Provider | None = None) -> dict | None:
    """``None`` when uiautomator confirms *xml*, else the refusal to return."""
    under, digest = dump_reuse.token(serial), _digest(xml)
    same = lambda x: _digest(x) == digest  # noqa: E731
    redo = functools.partial(a11y_early.reread, serial, inner, same)
    early, moved = await a11y_early.settle(serial, _packages(xml), redo)
    if early is not None:
        return early
    slow = await uiautomator_aside(serial, CONFIRM_SOURCE, a11y_early.CONFIRM_CAP_S)
    if slow.get("error"):
        step_timing.note(UNCHECKED_NOTE)
        return {"error": UNCHECKED_REFUSAL, "content": None}
    if compare(xml, str(slow.get("content") or "")).get("error"):
        a11y_reread.keep(serial, under, slow)
        _PKGS[serial] = _packages(xml)
        return _stale(serial, digest, moved)
    step_timing.note(CONFIRMED_NOTE)
    return None


async def _resume(serial: str, xml: str, judge: Confirm) -> dict | None:
    """While suspended: refuse the stale tree; once it moves, one confirmation."""
    if _digest(xml) == _SUSPENDED[serial]:
        step_timing.note(SUSPENDED_NOTE)
        return {"error": SUSPENDED_REFUSAL, "content": None}
    refused = None if _other_screen(serial, xml) else await judge()
    if refused is None:
        _SUSPENDED.pop(serial, None)
    return refused


async def _verdict(serial: str, read: tuple[str, int], judge: Confirm) -> dict | None:
    """``None`` when *xml* may be served, else the refusal (suspended, stale, unchecked)."""
    xml, started_under = read
    scrolled = a11y_scrolled.take(serial, started_under)  # our own swipe, no tap since
    if scrolled:
        _SUSPENDED.pop(serial, None)  # a swipe is an expected change: no stale refusal
    elif serial in _SUSPENDED:
        return await _resume(serial, xml, judge)
    digest = _digest(xml)
    if scrolled or not _repeats(serial, digest, started_under):
        return None
    refused = await a11y_clock.judged(serial, digest, judge)
    if refused is None:
        a11y_strikes.decay(serial)  # a healthy read
    return refused


def _serve(key: str, got: dict) -> dict:
    """Remember *got*'s tree as the last served."""
    digest = _digest(str(got.get("content")))
    _LAST[key] = (dump_reuse.token(key), digest)
    a11y_clock.served(key, digest)
    return got


async def checked(serial: str, inner: Provider) -> dict:
    """*inner*'s dump, refused when it repeats across an actuation and is not confirmed."""
    key = str(serial)
    started_under = dump_reuse.token(key)
    got = await inner(key)
    xml = str(got.get("content") or "")
    if got.get("error") or not xml:
        return got
    judge = functools.partial(_judge, key, xml, inner)
    refused = await _verdict(key, (xml, started_under), judge)
    if refused is None:
        return _serve(key, got)
    if refused.get("reread") is None:
        return refused
    _SUSPENDED.pop(key, None)  # the tree moved while we waited: a new screen
    return _serve(key, refused["reread"])


def guarded(inner: Provider) -> Provider:
    """*inner* behind :func:`checked`, the shape ``adb.set_dump_provider`` takes."""

    async def provider(serial: str) -> Any:
        return await checked(serial, inner)

    return provider
