"""Reply text of ``qa_prep_status``: the state line, the finalize advice and the
in-progress / already-finalized status shapes. Pure text assembly; the handler
owns the store reads."""

from __future__ import annotations

from typing import Any

from agents.host_echo import _unrecognized_names_line

# B1 (2026-08-21, TICKET-5138): the two prep_status states a polling host can switch
# on. A second editor process re-used a prep_id whose suite had finalized 11s
# earlier and polled qa_prep_status 122 times at 1s before giving up. The
# finalized reply DID name the suite -- but it is prose in a different shape from
# the status reply a loop parses, so there was no FIELD a loop could read as
# terminal.
PREP_STATE_IN_PROGRESS = "in_progress"
PREP_STATE_FINALIZED = "finalized"


# D3 (2026-08-21): "PRIMARY / ALTERNATIVE" against the prepare instruction's
# "recommended / a supported route, not a shortcut" was the honest-peers framing
# delivered by halves. Same recommendation, same words as host_mode.
def finalize_hint() -> str:
    """The finalize advice appended to the `qa_prep_status` reply (Fix 2)."""
    return (
        "\nRECOMMENDED finalize (Path A, crash-safe, keeps your review, and "
        "the only route the server duplicate prescreen runs on): when "
        "ready=yes, call `qa_submit_suite` with the small review SIDECAR "
        "object described in your preparation instructions (no "
        "`test_cases`). Finalizing with an empty `suite_json` instead is "
        "equally crash-safe but FORFEITS the duplicate review -- so send the "
        "sidecar even when you found nothing, because an EMPTY review field "
        "still counts as a review. The SUPPORTED ALTERNATIVE "
        "(Path B, one merged `suite_json`) does not need ready=yes, but "
        "nothing is saved until that single call, so an interrupted chat "
        "loses every category."
    )


def state_line(state: str) -> str:
    """The one machine-readable field a polling host can switch on.

    A NEW bullet: `ready to finalize (Path A)`, `staged`, `missing` and
    `unrecognized names` keep their exact wording on the non-finalized path, and
    the finalized path emits NONE of them (tests/test_prep_finalized_stamp.py)."""
    return f"- **state:** {state}\n"


def finalized_reply(prep_id: str, finalized_note: str) -> str:
    """The already-finalized notice, in the prep-status SHAPE, with a terminal
    state. ``finalized_note`` is the handler's finalized reply, used unchanged."""
    return (
        f"## Prep status (`{prep_id}`)\n\n"
        + state_line(PREP_STATE_FINALIZED)
        + "- **terminal:** yes -- this prep will never change again. STOP polling "
        "`qa_prep_status` for this prep_id; nothing is pending.\n\n" + finalized_note
    )


def _name_list(names: Any) -> str:
    return ", ".join(f"`{n}`" for n in names or []) or "(none)"


def status_reply(prep_id: str, status: dict) -> str:
    """Render the in-progress prep-status reply from the status view."""
    ready = "yes" if status.get("ready") else "no"
    # F6: the staged names are host-supplied, so this line is clipped (length,
    # fence, newlines) and its COUNT capped with the remainder disclosed.
    unrec_line = _unrecognized_names_line(
        status.get("unrecognized") or [],
        prefix="- **unrecognized names:** ",
        suffix="\n",
    )
    return (
        f"## Prep status (`{prep_id}`)\n\n"
        + state_line(PREP_STATE_IN_PROGRESS)
        + f"- **ready to finalize (Path A):** {ready}\n"
        f"- **staged:** {status.get('staged_count', 0)}/"
        f"{status.get('expected_count', 0)} — {_name_list(status.get('staged'))}\n"
        f"- **missing:** {_name_list(status.get('missing'))}\n"
        + unrec_line
        + finalize_hint()
    )
