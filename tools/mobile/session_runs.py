"""Mobile session: run planning, resumption and readback (resolve, list_runs). Cut verbatim from tools/mobile/session.py (P4.4).

The planning, signature, case and context code lives in the ``sruns_*`` leaves;
this module keeps the names every importer and test patches, plus the resume
offer and the thin readers that read ``resolve`` and ``list_runs`` here.
"""

from __future__ import annotations

import logging
from typing import Any  # noqa: F401

from tools.mobile import (  # noqa: F401
    explore_runner,
    importers,
    run_store,
    scheduler,
    sdk_locator,
)
from tools.mobile.replay.context import Context  # noqa: F401
from tools.mobile.session_core import (  # noqa: F401
    LANE_EXPLORE,
    LANE_SUITE,
    mint_run_id,
)
from tools.mobile.session_records import (  # noqa: F401
    capture_record,
    device_record,
    locale_record,
    reset_app_record,
)
from tools.mobile.session_resolve import (  # noqa: F401
    _is_abandoned,
    _lane_progress,
    _lease_age,
    _resolve_body,
    resolve,
)
from tools.mobile.sruns_cases import (  # noqa: F401
    _case_dumps,
    load_case,
    load_run_cases,
)
from tools.mobile.sruns_ctx import context_for, coverage_line  # noqa: F401
from tools.mobile.sruns_manifest import (  # noqa: F401
    _explore_manifest,
    _no_case_survived,
    _run_records,
    _suite_manifest,
)
from tools.mobile.sruns_plan import plan_explore_run, plan_suite_run  # noqa: F401
from tools.mobile.sruns_sig import case_signature, run_signature  # noqa: F401
from tools.mobile.sruns_types import (  # noqa: F401
    CaseSets,
    ExploreOpts,
    RunEnv,
    RunTarget,
    SuiteOpts,
)

logger = logging.getLogger("tools.mobile.session")


def import_cases(source: object) -> dict:
    """A tester's own cases -> ``{cases, rejected, truncated}``. Never raises."""
    return importers.load(source)


# The newest UNFINISHED run of the same app and the same cases, or None.
#
# Deliberately keyed on package plus case set and NOT on the serial. A run is
# a durable thing on disk; a serial is which emulator happened to be attached
# when it started. A tester whose emulator was restarted, or who is resuming
# from another chat, still wants the run they were in the middle of --
# ``_mobile_resume_device_stage`` re-checks the device on every resume, and
# the lane's device lock already serialises concurrent starts, so ignoring
# the serial here adds no hazard.
#
# The *limit* is a real bound, stated rather than hidden: the newest 60 runs
# are considered, and a resumable run older than that is not offered. It is
# an offer, not a guarantee, and `qa_mobile_status` still lists everything.
#
# Never raises: this decides whether to OFFER something, and a lookup that
# failed must fall through to planning a run rather than refusing one.
def find_resumable_run(signature: str, limit: int = 60) -> dict | None:
    """The newest UNFINISHED run of the same app and the same cases, or None."""
    try:
        wanted = str(signature or "")
        if not wanted:
            return None
        for row in (list_runs(limit) or {}).get("content") or []:
            offer = _resumable_offer(row, wanted)
            if offer:
                return offer
        return None
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile.session.find_resumable_run failed")
        return None


def _offer_of(run_id: str, resolved: dict) -> dict:
    """The resume offer for one unfinished run."""
    return {
        "run_id": run_id,
        "state": str(resolved.get("state") or ""),
        "done": int(resolved.get("done") or 0),
        "total": int(resolved.get("total") or 0),
    }


# THE KEPT SIGNATURE, and only that one.
#
# There was a second comparison here against `source_signature` --
# the cases the tester supplied before the filters -- so that a
# re-run of failures could recognise its own earlier run. Round 3
# showed it had to be conditioned on "that run was started with no
# filter", because otherwise a tester who ran five cases filtered
# to two and then started the whole suite was told the earlier run
# was "running the same cases": it was running two of five, and
# accepting the offer finishes with three never executed and a
# report that looks complete.
#
# And with that condition the clause is DEAD, which mutation then
# proved: a run started with no filter has the two signatures EQUAL
# by construction, so the kept comparison has already matched
# whenever the source one could. An unkillable guard is not a
# guard, so it is gone and `source_signature` stays on the manifest
# as the record of what was asked for.
#
# NO package argument at all. There was a package comparison here
# and mutation proved it unreachable -- `case_signature` is seeded
# WITH the package, so a signature match already implies it -- and
# round 2 then pointed out that keeping the PARAMETER after
# deleting its use is a trap for a caller who passes a package that
# did not build the signature. The binding is in the signature, and
# `test_a_different_package_gives_a_different_signature` pins it.
def _resumable_offer(row: dict, wanted: str) -> dict | None:
    """The offer for one listed run when it matches *wanted* and is unfinished."""
    manifest = row.get("manifest") or {}
    if wanted != str(manifest.get("case_signature") or ""):
        return None
    run_id = str(row.get("run_id") or "")
    resolved = (resolve(run_id) or {}).get("content") or {}
    if not resolved or resolved.get("finished"):
        return None
    return _offer_of(run_id, resolved)


def summary(run_id: str) -> dict:
    """Every checkpoint body for a run, newest verdicts included."""
    return run_store.list_cases(run_id)


def list_runs(limit: int = 10) -> dict:
    return run_store.list_runs(limit)
