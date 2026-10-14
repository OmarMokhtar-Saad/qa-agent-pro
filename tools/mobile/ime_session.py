"""The QA IME, made ready for a run and handed back afterwards.

**Why this module exists at all.** `ime.py` has had `remember_previous` and
`restore_previous` since the keyboard was pinned, and until now NOTHING called
either: the lane never selected the QA IME automatically, so a tester whose own
script used ADBKeyBoard typed into a keyboard whose receiver never registers on
API 35 and got silence. Measured on a second Mac, 2026-09-17. This module is the
first caller of both, and the sequence lives in one place so the order -- remember
BEFORE anything changes -- cannot be got wrong by a second caller written later.

**It takes no new setting.** No flag gates the QA keyboard at all:
`downloader.download` exempts the PINNED IME asset from
`QA_MOBILE_HTTPS_CAPTURE_ENABLED`, which gates only the HTTPS-capture proxy
download and the capture CA-certificate install, and installing on the device
is a device effect, which this lane never flag-gates. `apply=true` still gates
the run. Per CLAUDE.md's flag policy, an improvement ships ON with no flag of
its own; `apply` is the gate here.

House rules obeyed: no `print`, and every public function returns
``{"error", "content"}`` rather than raising.
"""

from __future__ import annotations

import asyncio  # noqa: F401
import logging
import os  # noqa: F401
import time  # noqa: F401
from typing import Any  # noqa: F401

from tools.mobile import ime, run_records, run_store  # noqa: F401
from tools.mobile.imes_back import (
    IME_VERIFY_TIMEOUT_S,
    read_back,
    restore,
    verify_typed,
)
from tools.mobile.imes_poll import (
    IME_POLL_INTERVAL_S,
    IME_POLL_MAX_ATTEMPTS,
    IME_RETRY_POLL_S,
    IME_SELECT_RETRY_DELAY_S,
)
from tools.mobile.imes_ready import IME_READY_TIMEOUT_S, _ensure_ready, ensure_ready
from tools.mobile.imes_record import (
    FALLBACK_FILE,
    FALLBACK_TTL_S,
    RECORD_FILE,
    clear_record,
    read_fallback,
    read_record,
    write_fallback,
    write_record,
)
from tools.mobile.imes_select import IME_SELECT_MAX_ATTEMPTS
from tools.mobile.imes_stale import restore_stale
from tools.mobile.imes_state import state
from tools.mobile.imes_sweep import (
    IME_STALE_SCAN_RUNS,
    pid_is_provably_dead,
    stale_records,
)
from tools.untrusted import wrap_untrusted  # noqa: F401

logger = logging.getLogger(__name__)

__all__ = [
    "FALLBACK_FILE",
    "FALLBACK_TTL_S",
    "IME_POLL_INTERVAL_S",
    "IME_POLL_MAX_ATTEMPTS",
    "IME_READY_TIMEOUT_S",
    "IME_RETRY_POLL_S",
    "IME_SELECT_MAX_ATTEMPTS",
    "IME_SELECT_RETRY_DELAY_S",
    "IME_STALE_SCAN_RUNS",
    "IME_VERIFY_TIMEOUT_S",
    "RECORD_FILE",
    "_ensure_ready",
    "clear_record",
    "ensure_ready",
    "pid_is_provably_dead",
    "read_back",
    "read_fallback",
    "read_record",
    "restore",
    "restore_stale",
    "stale_records",
    "state",
    "verify_typed",
    "write_fallback",
    "write_record",
]
