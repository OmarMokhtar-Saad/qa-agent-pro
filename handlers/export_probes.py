"""Read-only probes of the running install (moved out of handlers.export)."""

from __future__ import annotations

import logging
from pathlib import Path

import tools.updater as updater
from handlers import _common

logger = logging.getLogger("tools.mcp_handlers")


def _code_changed_since_start() -> bool:
    """True when the installed VERSION differs from the one this process loaded.

    Content-based, not mtime-based: a touched-but-identical VERSION must not
    trigger a reload loop. Never raises -- an unreadable VERSION reads as
    unchanged, so a failure can only SKIP a reload, never cause a spurious one.
    """
    try:
        on_disk = updater._local_version(Path(updater._INSTALL_DIR))
        return bool(
            on_disk and _common.BOOT_VERSION and on_disk != _common.BOOT_VERSION
        )
    except (OSError, ValueError):
        logger.debug("could not compare the installed version", exc_info=True)
        return False


# True when the install's .env carries DIFFERENT settings than this process
# booted with.
#
# config/settings parses .env exactly once at startup, so an edit made while
# the server is running has NO effect until the process is replaced. Callers
# use this to schedule the reload that applies it.
#
# CONTENT, not mtime (2026-08-09). Two things rewrite a running install's .env
# without changing a single setting: updater.migrate_env(), which appends a
# dated banner of newly shipped keys after an update, and env_heal.heal_env(),
# which qa-doctor runs. Under the mtime check EITHER rewrite looked like a
# tester edit, so the server scheduled a reload for work it had just done to
# itself. This is the same fix Batch A applied to the dist launcher's watchdog
# (`_env_fingerprint` in scripts/build_dist.LAUNCHER_TEMPLATE) and the rule
# `_code_changed_since_start` already applies to VERSION.
#
# Never raises: a missing or unreadable .env fingerprints as "" and reads as
# unchanged, so a failure here can only ever SKIP a reload, never trigger a
# spurious one.
def _env_changed_since_start() -> bool:
    """True when the install's .env carries DIFFERENT settings than this process
    booted with."""
    current = _common._env_fingerprint()
    if not current:
        return False
    return current != _common.BOOT_ENV_FINGERPRINT


def _edition_facts() -> dict:
    """The edition facts ``qa_machine_report`` injects into its backend section.

    One dict expression, so a raising fact gives no dict at all (the report
    then shows ``{}``) rather than half of one. Reads both functions as module
    globals at call time, so a test patch on either is what the reply carries.
    """
    return {
        "test_cases_only": bool(_common.test_cases_only()),
        "mobile_modules_present": bool(_common.mobile_modules_present()),
    }


_TEST_CASES_ONLY_NOTICE = (
    "⚠️ This edition generates test cases only — this tool is not available. "
    "Use qa_generate_test_cases or qa_export_suite."
)
