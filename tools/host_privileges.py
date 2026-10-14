"""Does THIS host let THIS account elevate -- asked BEFORE anything is proposed.

Top level, deliberately NOT under ``tools/mobile/``: ``mcp_handlers._TOOL_INFO``
and qa-doctor are not mobile, and a test-cases-only edition ships no
``tools/mobile/`` on disk at all, so a mobile-resident producer would be
unreachable exactly where plain ``adb`` install advice is printed.

This module is the facade: the seams tests patch (``_run``, ``_slice``,
``_host_os``, ``_CACHE``, ``probe``) are defined here; the verdicts, the data
and the rendering live in the ``tools.hpriv_*`` leaves, where the rest of the
original module docstring is kept as a comment block (``hpriv_consts``).
"""

from __future__ import annotations

import logging
import os  # noqa: F401  (patched by tests)
import platform
import subprocess  # noqa: F401  (patched by tests)
import sys
import time
from typing import cast

from config.settings_guard import never_raise
from tools.hpriv_consts import (  # noqa: F401  (the legacy surface)
    _DENIED_MARKERS,
    _SUDOER_MARKERS,
    ADMIN_SID,
    DISPLAY,
    MACOS,
    OTHER,
    REFRESH_HINT,
    WINDOWS,
)
from tools.hpriv_refs import REFERENCES  # noqa: F401
from tools.hpriv_render import (  # noqa: F401
    _attemptability_answer,
    attemptability,
    build_content,
    verdict_sentence,
)
from tools.hpriv_steps import STEPS  # noqa: F401
from tools.hpriv_verdict import Ask, posix_verdict, windows_verdict
from tools.hpriv_verdict import budget_spent as _budget_spent  # noqa: F401
from tools.hpriv_verdict import windows_group as _windows_verdict_group  # noqa: F401
from tools.process_runner import RunOpts, run

logger = logging.getLogger(__name__)

#: PER-SUBPROCESS timeout, passed to every ``_run`` call the way
#: ``mobile.platform_info._run_sync`` takes its ``timeout`` argument -- the
#: pattern this repo already uses, reused rather than reinvented. This bounds
#: ONE call, NOT the probe: the Windows path runs two of them in sequence, so
#: ``PROBE_BUDGET_S`` below is what bounds the wait the caller actually feels.
PROBE_TIMEOUT_S = 5

#: The WHOLE probe's wall-clock budget, and the only number a caller can reason
#: about. Checked against a single deadline set once in :func:`probe`, so adding
#: a third subprocess later cannot widen the wait without changing this line:
#: each ``_run`` is given ``min(PROBE_TIMEOUT_S, remaining budget)`` and a probe
#: with no budget left returns UNDETERMINED instead of starting another call.
#: Smaller than 2x the per-call timeout deliberately -- the aggregate is the
#: promise, and a sum-of-parts bound is not one.
PROBE_BUDGET_S = 8

#: Per-process cache. Replaced wholesale by ``probe(refresh=True)``.
_CACHE: dict | None = None


# ONE subprocess seam: no shell, a mandatory timeout, and never raises.
#
# ``timeout`` is a parameter rather than a constant read inside, mirroring
# ``mobile.platform_info._run_sync``, so a caller holding the probe's shared
# deadline can hand down whatever is LEFT of the budget instead of granting a
# fresh full timeout to every call in a sequence.
#
# Tests patch this single name, so no test ever queries the real machine, and
# an rc of -1 is the module's own "could not ask" -- distinct from a real
# non-zero rc, which is evidence.
def _run(argv: list[str], timeout: float = PROBE_TIMEOUT_S) -> tuple[int, str, str]:
    """ONE subprocess seam: no shell, a mandatory timeout, never raises."""
    with never_raise(
        logger, "host_privileges probe could not run %s: %s", argv[:1], with_exc=True
    ) as g:
        r = run(
            list(argv),
            timeout=max(0.1, float(timeout)),
            opts=RunOpts(shell=False),
        )
        if r.exc is not None:
            raise r.exc
        out = (r.stdout or b"").decode("utf-8", "replace")
        err = (r.stderr or b"").decode("utf-8", "replace")
        return cast(int, r.rc), out, err
    return -1, "", str(g.exc)


def host_os() -> str:
    if sys.platform == "win32":
        return WINDOWS
    if sys.platform == "darwin":
        return MACOS
    return OTHER


def _slice(deadline: float) -> float:
    """What is LEFT of the probe's ONE budget, capped by the per-call timeout.

    Every subprocess in this module is started with this, so the sum of the
    calls cannot exceed ``PROBE_BUDGET_S`` no matter how many there are. A
    non-positive result means the budget is spent and the caller must return
    UNDETERMINED rather than start another process.
    """
    return min(float(PROBE_TIMEOUT_S), deadline - time.monotonic())


def _posix_verdict(deadline: float) -> dict:
    return posix_verdict(Ask(_run, _slice), deadline)


def _windows_verdict(deadline: float) -> dict:
    return windows_verdict(Ask(_run, _slice), deadline)


# ONE deadline for the WHOLE probe, set here and handed down, because
# the Windows path runs two subprocesses in sequence and a per-call
# timeout bounds neither of the things a caller cares about: the wait
# it will actually feel. See PROBE_BUDGET_S.
def _fresh() -> dict:
    """Probe once and build the ``content`` dict."""
    deadline = time.monotonic() + float(PROBE_BUDGET_S)
    os_name = host_os()
    if os_name == WINDOWS:
        core = _windows_verdict(deadline)
    else:
        core = _posix_verdict(deadline)
    return build_content(core, os_name, str(platform.machine() or ""))


def clear_cache() -> None:
    """Drop the per-process probe cache (the next ``probe`` runs cold)."""
    global _CACHE
    _CACHE = None


def probe(refresh: bool = False) -> dict:
    """``{"error", "content"}`` -- the shape used across ``tools/``. Never raises.

    Cached per process, and the reply SAYS so: ``content["cached"]`` plus
    ``content["refresh_hint"]``. ``refresh=True`` discards the cache and probes
    again, because the server outlives the machine's configuration.
    """
    global _CACHE
    with never_raise(
        logger, "host_privileges.probe failed", level=logging.ERROR, exc_info=True
    ) as g:
        if _CACHE is not None and not refresh:
            cached = dict(_CACHE)
            cached["cached"] = True
            return {"error": None, "content": cached}
        content = _fresh()
        _CACHE = dict(content)
        return {"error": None, "content": content}
    return {"error": str(g.exc), "content": None}
