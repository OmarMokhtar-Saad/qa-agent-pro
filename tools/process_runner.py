"""One subprocess runner for the legacy ``_run`` helpers (stdlib only, a leaf).

Four modules each hand-rolled the same spawn / wait / kill dance with small,
test-pinned differences. This module holds the shared core; every legacy
``_run`` stays a thin, patchable delegate that maps the :class:`RunResult` back
to its exact old contract (return tuple, raised exception, log text).

Rules the core keeps so that a delegate is byte-for-byte the old behaviour:

* A keyword reaches ``subprocess.run`` / ``asyncio.create_subprocess_exec`` /
  ``communicate`` only where the legacy site passed it. ``None`` means "not
  passed"; nothing is added for safety.
* The stdlib is called through module attributes (``subprocess.run``,
  ``asyncio.create_subprocess_exec``, ``asyncio.wait_for``) so a test that
  patches those shared objects still intercepts.
* The core never logs and never reads a clock; delegates own both.
* The core returns failures in :class:`RunResult` instead of raising, with
  exactly two intentional raises, both legacy-preserving: ``CancelledError``
  after the kill (``kill_on_cancel``), and anything a caller-supplied hook
  (``on_spawned`` / ``on_drain_error``) raises. A kill of an already-dead child
  (``ProcessLookupError``) is swallowed. ``arun`` starts the child in its own
  session and kills the whole process group, so grandchildren die with it.
"""

from __future__ import annotations

import asyncio  # noqa: F401  (the legacy surface; tests patch through it)
import os  # noqa: F401
import signal  # noqa: F401
import subprocess  # noqa: F401
import sys  # noqa: F401
from collections.abc import Callable  # noqa: F401
from dataclasses import dataclass, field  # noqa: F401
from typing import Any, Literal, TypedDict  # noqa: F401

from tools.prun_async import (  # noqa: F401
    _kill,
    _kill_now,
    _new_session_kwargs,
    _pending,
    arun,
)
from tools.prun_sync import run  # noqa: F401
from tools.prun_types import (  # noqa: F401
    CREATE_NO_WINDOW,
    ArunOpts,
    KillPlan,
    NoWindowKwargs,
    RunOpts,
    RunResult,
    _NewSessionKwargs,
    no_window_kwargs,
)
