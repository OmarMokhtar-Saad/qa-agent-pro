"""Mobile session: the manifest keys a new run starts with (cut from session_runs.py, C5 w17)."""

from __future__ import annotations

import logging

from tools.mobile import sdk_locator
from tools.mobile.session_core import LANE_EXPLORE, LANE_SUITE
from tools.mobile.session_records import (
    capture_record,
    device_record,
    locale_record,
    reset_app_record,
)
from tools.mobile.sruns_cases import _case_dumps
from tools.mobile.sruns_sig import case_signature
from tools.mobile.sruns_types import CaseSets, RunEnv, RunTarget

logger = logging.getLogger("tools.mobile.session")


def _no_case_survived(ordered: dict) -> dict:
    """The refusal for a filter set that dropped every case."""
    applied = ", ".join((ordered.get("content") or {}).get("applied") or []) or "none"
    return {
        "error": (
            f"No case survived the filters, so no run was created (applied: {applied})."
        ),
        "content": None,
    }


# WHAT LANGUAGE THIS RAN IN, on the manifest for the same reason
# `device` is: a finished run must still say so after the device
# is gone, and two readers asking the device again could get two
# answers.
# WHAT HARDWARE THIS RAN ON, recorded once at planning time.
# On the manifest rather than re-read per status call because a
# finished run must still say what it ran on after the device is
# unplugged, and because two readers asking the device again
# could get two answers.
# WHAT TIER THIS RUN'S API CAPTURE REACHED, recorded once at
# planning time for the same reason `locale`/`device` are: a
# finished run must still say so after the proxy is torn down.
def _run_records(env: RunEnv) -> dict:
    """The four environment records both lanes put on a new run's manifest."""
    return {
        "locale": locale_record(env.locale),
        "device": device_record(env.device),
        "capture": capture_record(env.capture),
        "reset_app": reset_app_record(env.reset_app),
    }


# The cases the TESTER supplied, before the filters -- the
# RECORD of what was asked for, and nothing more.
#
# It was added so a re-run of failures could recognise its own
# earlier run, and `find_resumable_run` no longer reads it: that
# match had to be conditioned on "started with no filter", and
# with that condition it was dead. The flow survives anyway,
# because a run's manifest stores the KEPT cases, so a second
# re-run hashes those and matches `case_signature`.
# (applies to the "source_signature" key)
def _suite_manifest(sets: CaseSets, target: RunTarget, source: str) -> dict:
    """The suite lane's own manifest keys (identity, signatures, kept cases)."""
    package = target.package
    return {
        "lane": LANE_SUITE,
        "package": str(package or ""),
        "serial": str(target.serial or ""),
        "source": str(source or ""),
        "case_signature": case_signature(package, sets.kept),
        "source_signature": case_signature(package, sets.submitted),
        "cases": _case_dumps(sets.kept),
    }


# "system_image" is a SNAPSHOT of the PRODUCER's answer, not of a setting.
# sdk_locator.avd_system_image() is the one function that says which
# image this lane runs: it reads image.sysdir.1 out of the AVD's own
# config.ini, so it reports what the device on disk actually boots.
# Reading qa_mobile_system_image_tag here would be a SECOND derivation
# -- and since the tester brings their own AVD, no setting decides it.
# "" when the AVD cannot be resolved, which the report renders as "not
# recorded" rather than inventing today's answer for a finished run.
def _explore_manifest(state: dict, target: RunTarget) -> dict:
    """The exploratory lane's own manifest keys."""
    avd = target.avd
    return {
        "lane": LANE_EXPLORE,
        "package": str(target.package or ""),
        "serial": str(target.serial or ""),
        "avd": str(avd or ""),
        "system_image": str(
            (sdk_locator.avd_system_image(avd) or {}).get("content") or ""
        ),
        "order": [],
        "total": 0,
        "cases": [],
        "explore": state,
    }
