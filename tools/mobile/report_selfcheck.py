"""Pins the rendered report against the run store, and exits non-zero on any miss.

WHY THIS IS NOT A TEST: it runs against a REAL run directory on a tester's
machine, after a real run, which no unit test can do. The suite proves the
renderer is correct on fixtures; this proves the artifact on disk is complete.

THE SHAPE THAT MATTERS. A pin that a TRUNCATED page can satisfy is worse than
no pin, because it certifies the artifact it failed to read. So every
completeness pin here is an EQUALITY or a terminator, never a containment:

* ``cases_exactly_once`` -- the set of ``data-tc`` values in the page EQUALS the
  set of ``tc_id``s in the store, and no id appears twice. A page that dropped
  half its cards fails on the equality; a page that duplicated one fails on the
  count. Two independent halves, because one mutation cannot prove both.
* ``totals_match`` -- the page's ``data-*`` totals are compared against a tally
  RECOMPUTED from the store, not against the page's own numbers.
* ``card_count_matches_total`` -- the parsed card count equals ``data-total``.
* ``terminator_present`` -- the document ends with the ``qa-report-end`` marker
  carrying the card count. Truncation removes it.

OUTPUT GOES TO ``sys.stdout.write``, not ``print`` (house rule: no bare
``print``) and not ``logging``. This is a CLI whose stdout IS its product -- the
programme spec asks for its output to be pasted as evidence -- and ``logging``
would route it to stderr under a configuration the CALLER owns, which would
make the required evidence depend on the caller's log setup.
"""

from __future__ import annotations

import logging  # noqa: F401
import sys  # noqa: F401
from html.parser import HTMLParser  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any  # noqa: F401

from tools.mobile import report, run_store  # noqa: F401
from tools.mobile.rchk_cli import USAGE, main  # noqa: F401
from tools.mobile.rchk_consts import (  # noqa: F401
    EXTERNAL,
    FORBIDDEN_MARKERS,
    MEDIA_TAGS,
    PIN_CAPTURE_TIER,
    PIN_CARD_COUNT,
    PIN_CASES,
    PIN_CRASH,
    PIN_NO_ASSET,
    PIN_NO_MARKERS,
    PIN_NO_SCRIPT,
    PIN_NO_SECRET,
    PIN_NO_XML,
    PIN_SIZE,
    PIN_TERMINATOR,
    PIN_TOTALS,
    PINS,
    RAW_XML,
    SECRET_TOKENS,
    SHOT_SRC,
)
from tools.mobile.rchk_crash import _pin_capture_tier, _pin_crashes  # noqa: F401
from tools.mobile.rchk_links import _pin_links, _stray_images  # noqa: F401
from tools.mobile.rchk_page import _Page  # noqa: F401
from tools.mobile.rchk_pins import (  # noqa: F401
    _as_int,
    _pin,
    _pin_absent,
    _pin_card_count,
    _pin_cases,
    _pin_script,
    _pin_terminator,
    _pin_totals,
)
from tools.mobile.rchk_run import _all_pins, check  # noqa: F401

logger = logging.getLogger(__name__)

if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
