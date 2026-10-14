"""The numbered "Pick one" options of a volume refusal."""

from __future__ import annotations

from agents.host_prep_volume import VolumeCheck, VolumeVerdict

VOLUME_OPT_ACK = (
    "Or, ONLY if the tester has seen these numbers and confirms "
    "a smaller suite is right for this feature, resubmit unchanged "
    "with `volume_floor_ack=true`. Ask them first -- do not decide "
    "that on your own judgement."
)


def volume_rebuild_tail(verdict: VolumeVerdict, prep_id: str) -> str:
    """The full-rebuild option after its lead-in, shared by both orderings."""
    return (
        "resubmit the COMPLETE suite with the SAME prep_id "
        f"`{prep_id}` -- the suite needs {verdict.floor_total} case(s) overall. "
        f"`categories[].min_cases` asks {verdict.floor} per category and that "
        "is the right target for each, but a category with genuinely "
        "less to test may come in under it PROVIDED another covers "
        "the difference; what may not stand is the total, an empty "
        "category, or one below half its share. The number came from "
        "THIS feature's own complexity, not a fixed floor."
    )


def volume_staged_topup(check: VolumeCheck) -> str:
    """The top-up option when the server already HOLDS staged categories."""
    prep_id = check.prep_id
    return (
        "**Top up per category -- this is the route here.** "
        f"{check.staged} categor"
        + ("y is" if check.staged == 1 else "ies are")
        + f" already staged on prep `{prep_id}`, so **do NOT resend "
        "the cases you already sent**. Call `qa_submit_category` "
        "again for the SHORT categories ONLY (a repeat call REPLACES "
        "that category's staged row, so send that category's full "
        f"set), then call `qa_submit_suite` with prep_id `{prep_id}` "
        "and " + check.phrases.short + " -- the finalize rebuilds the suite "
        "from the staged rows. `qa_prep_status` shows the set. "
        "Regenerating the whole suite instead costs a second full "
        "pass AND silently changes cases the tester already reviewed."
    )


def _topup_first(verdict: VolumeVerdict, check: VolumeCheck) -> list:
    rebuild = volume_rebuild_tail(verdict, check.prep_id)
    return [
        volume_staged_topup(check),
        "Or, only if you would rather rebuild every category from "
        "scratch: generate the missing cases and " + rebuild,
        VOLUME_OPT_ACK,
    ]


def _rebuild_first(verdict: VolumeVerdict, check: VolumeCheck) -> list:
    rebuild = volume_rebuild_tail(verdict, check.prep_id)
    return [
        "Generate the missing cases and " + rebuild,
        "Or top up per category: call `qa_submit_category` again for "
        "each short category (a repeat call REPLACES that category's "
        "staged row, so send its full set), then "
        + check.phrases.long
        + ". `qa_prep_status` shows the set.",
        VOLUME_OPT_ACK,
    ]


def volume_options(verdict: VolumeVerdict, check: VolumeCheck) -> str:
    """The numbered "Pick one" list of a refusal.

    ``check.staged`` reorders it: when the server already holds the cases the
    top-up route LEADS and the full rebuild is demoted to the fallback it is.
    """
    if check.staged:
        options = _topup_first(verdict, check)
    else:
        options = _rebuild_first(verdict, check)
    return "".join(f"{i}. {opt}\n" for i, opt in enumerate(options, 1))
