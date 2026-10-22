"""The run listing for ``mobile_device``: one line per run on this machine."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from handlers.mobile import device_setup_rows
from tools.mobile import session_runs

logger = logging.getLogger("tools.mcp_handlers")


def _mobile_status_listing() -> str:
    """The status reply when no run id was given: the newest runs on disk."""

    # A device being set up has no run dir yet: list it first.
    setting_up = "\n".join(device_setup_rows.setup_rows())
    if setting_up:
        setting_up += "\n\n"
    runs = (session_runs.list_runs(10) or {}).get("content") or []
    if not runs:
        return (
            setting_up + "## No mobile runs yet\n\nCall `qa_mobile_test` to start one."
        )
    listed = "\n".join(_mobile_run_row(row) for row in runs)
    return setting_up + "## Mobile runs on this machine\n\n" + listed


def _run_row_head(row: object) -> tuple[str, str]:
    """``(run_id, plain line)``: the lane and case count every row has."""
    body = row if isinstance(row, dict) else {}
    manifest = body.get("manifest") or {}
    run_id = str(body.get("run_id") or "?")
    line = (
        "- `"
        + run_id
        + "` — "
        + str(manifest.get("lane") or "?")
        + " lane, "
        + str(manifest.get("total") or 0)
        + " case(s)"
    )
    return run_id, line


def _state_suffix(run_id: str) -> str:
    """``", state **x**"`` for a run that resolves, else ``""``."""

    resolved = (session_runs.resolve(run_id) or {}).get("content") or {}
    if not resolved:
        return ""
    return ", state **" + str(resolved.get("state") or "unknown") + "**"


def _verdict_suffix(run_id: str) -> str:
    """The newest verdict of the run, or that none was given yet, or ``""``."""

    recorded = [
        case
        for case in ((session_runs.summary(run_id) or {}).get("content") or [])
        if isinstance(case, dict)
    ]
    verdicts = [str(case.get("verdict") or "") for case in recorded]
    verdicts = [verdict for verdict in verdicts if verdict]
    if verdicts:
        return ", last verdict " + verdicts[-1]
    return ", no verdict yet" if recorded else ""


def _run_row_enrich(run_id: str, line: str) -> str:
    """*line* plus the run's state and newest verdict; a failure keeps what
    was added so far."""
    with never_raise(
        logger,
        "mcp mobile: could not enrich the listing row for %s",
        run_id,
        level=logging.INFO,
    ):
        line += _state_suffix(run_id)
        line += _verdict_suffix(run_id)
    return line


# E5 (2026-09-04): the listing gave the lane and a case count, so a tester
# looking for the run they had abandoned could not tell it from the seven
# others -- and neither could the model, which started an eighth. The state
# and the newest verdict are what distinguish them.
#
# Never raises and never lets a bad run break the listing: a row that cannot
# be resolved falls back to the plain line it always had.
def _mobile_run_row(row: object) -> str:
    """One line of the run listing: what it is, where it stands, what it said."""
    run_id, line = _run_row_head(row)
    return _run_row_enrich(run_id, line)
