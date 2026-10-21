"""The relay block a finished-run reply starts with, and the take-over notice.

Seventeenth layer of the ``render_*`` split of ``render.py``.

``relay_block`` -- the block a finished-run reply MUST start with. The calling
model is told, in the block itself, to relay it word for word -- never upgrade
it into a plainer claim ("Login succeeded") than the run itself can back.

Three facts only: the verdict, whether the reset the tester asked for (at
launch, or mid-run via ``clear_app_data``) actually landed, and the typed-field
tally when the run typed anything. Empty inputs still render -- a run with no
verdict computed yet reads ``unverified``, never blank.

``takeover_block`` -- wrap ``run_store.takeover_message`` and stop this chat
producing packets.

The wording is deliberately careful: the lease is read-decide-write with a
compare-after-swap that NARROWS the race rather than closing it, so this says
the other chat holds the run and that this one has stopped -- it does not claim
the other chat cannot be displaced in turn.
"""

from __future__ import annotations


def _reset_line(reset: object) -> str:
    reset = reset if isinstance(reset, dict) else {}
    requested = bool(reset.get("requested"))
    done = bool(reset.get("done"))
    if requested:
        return "reset: " + ("done" if done else "not done - guard stop not confirmed")
    return "reset: not requested"


def relay_block(
    *, verdict: str = "", reset: object = None, typed_field_line: str = ""
) -> str:
    """The block a finished-run reply MUST start with; relayed word for word."""
    lines = [
        "RELAY THIS BLOCK WORD FOR WORD to the tester. Never upgrade, soften "
        "or omit it. Do not repeat the tester's ID, password or one-time "
        "code in your message, and keep your own reasoning out of it.",
        "verdict: " + (str(verdict).strip() or "unverified"),
        _reset_line(reset),
    ]
    tally = str(typed_field_line or "").strip()
    if tally:
        lines.append(tally)
    return "\n".join(lines) + "\n\n"


def takeover_block(message: str) -> str:
    """Wrap ``run_store.takeover_message`` and stop this chat producing packets."""
    return (
        str(message)
        + "\n\nNo further packets are produced in this chat for that run. If the "
        "other chat is gone, call `qa_mobile_test` with the same `run_id` and "
        "no `session_token` to take it back."
    )
