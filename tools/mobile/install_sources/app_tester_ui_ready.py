"""App Tester UI: wait until a screen has finished drawing before judging it.

The fast accessibility reader answers before App Tester has drawn a page that
was resumed (no loading spinner shows): a read can hold no texts at all, or
only the page's header. Each wait here polls a cheap condition with a bound on
the number of reads; nothing pauses for a fixed time.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from tools.mobile.dialog_handlers import visible_texts
from tools.mobile.install_sources import app_tester_ui_memory as memory
from tools.mobile.install_sources.app_tester_ui_consts import (
    ACTION_LABELS,
    APP_TESTER_POLL_S,
    NO_ROWS_S,
    OPEN_LABEL,
    READ_BUDGET_S,
    SleepFn,
    _Run,
)
from tools.mobile.install_sources.app_tester_ui_funcs import parse_releases
from tools.mobile.install_sources.app_tester_ui_rows import is_listing

log = logging.getLogger(__name__)

#: Reads (one APP_TESTER_POLL_S apart) of an app page that has not finished
#: drawing (no release, no action button) before it is judged as it stands.
PAGE_READY_POLLS = 8
#: Reads of a screen that holds no text at all before it is judged empty.
BLANK_REREADS = 3


def page_ready(xml: str) -> bool:
    """True when the screen is a drawn app page: releases or an action button."""
    if is_listing(xml):
        return False
    texts = visible_texts(xml)
    labels = (*ACTION_LABELS, OPEN_LABEL)
    return bool(parse_releases(texts)) or any(t in texts for t in labels)


def on_own_page(run: _Run, xml: str) -> bool:
    """True when App Tester opened on the requested app's page, so the list is
    not needed. Only a package proven before (its App Tester id is remembered,
    the same trust as the link) is matched; any other page goes back."""
    package = run.request.package
    if is_listing(xml) or not memory.known_app_id(package):
        return False
    hit = package in visible_texts(xml)
    log.info("app_tester resumed page of %s: %s", package, hit)
    return hit


async def wait_page_ready(run: _Run, sleep: SleepFn) -> str:
    """Poll until the app page is drawn, at most PAGE_READY_POLLS reads. The
    last XML read ("" when no read worked)."""
    xml = ""
    end = time.monotonic() + READ_BUDGET_S
    for _ in range(PAGE_READY_POLLS):
        shot = await run.ui.screen()
        xml = shot.xml if shot is not None else ""
        if page_ready(xml):
            return xml
        log.info("app_tester page not drawn: %d texts", len(visible_texts(xml)))
        if time.monotonic() >= end:
            break
        await sleep(APP_TESTER_POLL_S)
    return xml


def _read_end(run: _Run) -> float:
    """When reads stop: READ_BUDGET_S from now, and while no list row has been
    read yet also no later than the NO_ROWS_S box of the attempt (H15)."""
    end = time.monotonic() + READ_BUDGET_S
    if run.offsets or run.card_tapped:
        return end
    return min(end, run.started + NO_ROWS_S)


async def read_drawn(run: _Run, sleep: SleepFn) -> Optional[str]:
    """The screen's XML once it holds any text, at most BLANK_REREADS reads.
    None when no read worked; an empty screen's XML when it stayed empty."""
    xml: Optional[str] = None
    end = _read_end(run)
    for _ in range(BLANK_REREADS):
        shot = await run.ui.screen()
        if shot is not None:
            xml = shot.xml
            if visible_texts(xml):
                return xml
            log.info("app_tester blank read: 0 texts")
        if time.monotonic() >= end:
            break
        await sleep(APP_TESTER_POLL_S)
    return xml
