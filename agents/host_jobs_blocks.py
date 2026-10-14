"""Host mode: disclosure blocks for a high or unverified ambiguity preflight (split from host_jobs.py)."""

from __future__ import annotations


def _ambiguity_high_block(result, notes: list) -> str:
    """The block for a host that reported ``high`` ambiguity and submitted."""
    out = [
        "> ⚠️  **Your chat model classified this ticket as "
        "`high` ambiguity and submitted anyway.** Step 0 said to stop and "
        "ask first, so the suite below was generated against a ticket its "
        "own reviewer judged too under-specified to test."
    ]
    qs = list(getattr(result, "questions", None) or [])
    out += [f">   - unanswered: {q}" for q in qs]
    out += [f">   - {n}" for n in notes]
    return "\n".join(out) + "\n\n"


_UNVERIFIED_TEXT = (
    "> ⚠️  **The ticket's testability was never verified.** "
    "The requirement pre-pass runs in your chat, not on "
    "this server -- and this submission came back with no readable "
    "`ambiguity_result`, so there is no evidence it ran at all. "
    # F7 (2026-08-15): say that the step was declared BLOCKING and
    # that this server cannot enforce it. The prepare payload marks
    # step 0 `blocking: true` in `jobs_to_run`, and submit accepts
    # the suite regardless -- an asymmetry a tester reading only
    # this block could not see, and which decides whether they read
    # "it did not run" as a server bug or as their host skipping a
    # step. 2026-08-29: this used to say the asymmetry was left
    # unfixed on purpose, because "a refusal would throw away
    # generation work the tester already paid for". That rationale
    # is retired -- the refusal keeps the prep and every staged row,
    # so it costs one round trip and no work at all, which is why
    # QA_HOST_AMBIGUITY_REQUIRE_RESULT now defaults ON. This block
    # therefore renders in TWO situations and must read correctly in
    # both: on an install that turned the refusal OFF, and prefixed
    # onto the refusal itself (mcp_handlers.py:7719), where telling
    # the reader to enable what is already enabled -- and pointing
    # at "the suite below", which was not returned -- would be
    # simply false.
    "The payload declared that step `blocking: true`, but this "
    "server has no way to enforce a step that runs inside your "
    "chat: it can only report that the evidence never came back. "
    "Where `QA_HOST_AMBIGUITY_REQUIRE_RESULT` is on -- the default "
    "since 2026-08-29 -- the submission is REFUSED and nothing was "
    "discarded: run step 0 and resubmit the same suite under the "
    "same prep_id. Where it has been turned off, any suite shown "
    "below is UNVERIFIED against an under-specified ticket."
    # 2026-08-09 (Batch 3, FIX 2): say the LOSS, not just the
    # process. Modelled on attested_image_gap_note and the nli_note,
    # which both refuse to claim a check that could not have happened.
    # This matters more than it reads: the server-side TICKET-7154 gate
    # is unconditionally SKIPPED -- the pre-pass is boomeranged since
    # 2026-08-12, when QA_HOST_AMBIGUITY_REVIEW_ENABLED was DELETED
    # and its ON behaviour hardcoded -- so an absent verdict means no
    # screening happened anywhere at all. The remedy named above is
    # the one that still exists; there is no longer a setting that
    # puts the check back on this server.
    " **This suite carries NO ambiguity screening**: the preflight "
    "did not run, so nothing checked whether the ticket is specified "
    "well enough to test. That is NOT the same as 'checked and found "
    "nothing' -- treat it as unscreened."
)


def _ambiguity_unverified_block(notes: list) -> str:
    """The block for a submission with no readable ``ambiguity_result``."""
    out = [_UNVERIFIED_TEXT]
    out += [f">   - {n}" for n in notes]
    return "\n".join(out) + "\n\n"
