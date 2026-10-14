"""A planner's script before it runs: folded, validated, refused or keyboard-ready."""

from __future__ import annotations

from tools.mobile import actions as actions_mod
from tools.mobile import ime_session, run_verdict
from tools.mobile.case_run_checkpoint import _checkpoint
from tools.mobile.case_run_consts import NEEDS_MODEL, TEXT_OPS
from tools.mobile.case_run_counts import escapes_used
from tools.mobile.case_run_types import _Case
from tools.mobile.replay.context import Context


def note_refusal(error: object) -> str:
    """Write the one INFO ``refused:`` line for a refused script and return it."""
    return run_verdict.log_refusal(error)


def script_types(script: object) -> bool:
    """Does this script put text on screen at any point?

    Asked of the WHOLE script before the replay starts, rather than on the first
    type op as it happens: making a keyboard ready is an install and three shell
    round trips, and doing that in the middle of a replay puts a minute of
    device work between two steps that a tester reads as one action.
    """
    for action in list(getattr(script, "actions", None) or []):
        if str(getattr(action, "op", "") or "") in TEXT_OPS:
            return True
    return False


def _fold_queue(raw_script: object, queued_in: list) -> tuple[object, str | None]:
    """Fold the queued actions into the submission: ``(script, refusal)``.

    ``refusal`` is ``None`` when the fold succeeded (or nothing was queued).
    """
    if not queued_in:
        return raw_script, None
    decoded = actions_mod.decode_reply(raw_script)
    combined = actions_mod.combine_queued_actions(
        queued_in, list(decoded.get("actions") or [])
    )
    if not combined.get("ok"):
        note_refusal(combined.get("reason"))
        return raw_script, str(combined.get("reason") or "")
    return {"actions": combined["actions"]}, None


def _parse_submission(raw_script: object, folded: bool) -> dict:
    """Validate the script; a folded queue was already held to both caps by the
    combine, so only a bare submission is held to the model cap here."""
    parsed = actions_mod.parse_script(
        raw_script,
        max_actions=(
            actions_mod.MAX_ACTIONS if folded else actions_mod.MAX_MODEL_ACTIONS
        ),
    )
    if parsed.get("error"):
        note_refusal(parsed["error"])
    return parsed


# The needs-model checkpoint for a script refused before any replay.
#
# A refused script is NOT an escape: nothing was replayed, so the planner
# gets the same screen back and one of its escapes is not spent on our own
# validation.
def _refused_script(case: _Case, reason: str, queued_in: list) -> dict:
    """The needs-model checkpoint for a script refused before any replay."""
    return {
        "error": None,
        "content": _checkpoint(
            case.run_id,
            case.tc_id,
            case.view,
            verdict="",
            status=NEEDS_MODEL,
            reason=reason,
            trace=[],
            escapes=escapes_used(case.run_id, case.tc_id),
            packet=None,
            queued_actions=queued_in,
        ),
    }


# THE KEYBOARD, BEFORE ANYTHING IS TYPED.
#
# `ime_session.ensure_ready` is idempotent, so a run whose every case types
# pays for this once; a run that never types never calls it and never
# displaces the tester's keyboard. A refusal ends the case by NAME rather
# than replaying into a keyboard that is not listening -- which is the
# silence this whole change exists to end: ADBKeyBoard's receiver never
# registers on API 35, so a tester's own script typed into nothing and
# reported success.
async def _ready_keyboard(script: object, ctx: Context, run_id: str) -> None:
    """THE KEYBOARD, BEFORE ANYTHING IS TYPED (see the note above)."""
    if not script_types(script):
        return
    ready = await ime_session.ensure_ready(ctx.serial, run_id)
    if ready.get("error"):
        # R1d: NO early return. The case runs with typing over stdin; the
        # replay attaches the fallback notice (why, and the exact fix) to
        # the result. The failure is memoised per run by ensure_ready.
        ctx.typing_fallback = str(ready["error"])[:400]
