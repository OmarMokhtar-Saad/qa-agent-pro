"""The closing "Choose ONE route" text of a category reply and its constants."""

from __future__ import annotations

from handlers import export

# F4 / F11 / Fix 2 text for the reply's closing "Choose ONE route" block. These
# were locals of `handle_submit_category`; they are module constants now so the
# text is written once and `_category_route` stays a few lines long.
#
# Only the duplicate review runs on the merged suite now that the host coverage
# review is deleted (2026-08-12), so name just it.
_CATEGORY_REVIEW_LABEL = "duplicate review"

# UNUSED (kept verbatim, see the plan's Follow-ups). The duplicate review runs
# ONLY on the merged suite, so the two routes are a real CHOICE, and the branch
# that builds the route must fire on that fact rather than on review_note: the
# note is empty until the host sends the field, which is impossible on this
# route, so the old condition steered away from the only route that works (F11).
# F11/F4 (iteration 4): this GENERAL "how to keep your review" explanation must
# NOT name a review FIELD by its literal token. The per-submission note
# (category_dedup_note) is the only place a field name may appear, and only when
# THIS submission's own payload carried it; naming it here too would read as "a
# review already ran" or "you were supposed to send it here". Pinned by
# test_submit_category_is_silent_without_the_field in
# tests/test_host_dedup_review.py. The field NAME is taught once, at prepare
# time, by host_mode._HOST_DEDUP_INSTRUCTION.
_CATEGORY_SIDECAR_LINE = (
    " The empty finalize is not the only option here: to KEEP "
    "the duplicate review on THIS route, finalize instead with "
    "the small review SIDECAR object described in your "
    "preparation instructions -- that review field alone, with "
    "empty or absent `test_cases`. The server remaps its tc_ids "
    "across the merge, so staging categories does NOT forfeit "
    "that review. Send it even when you found nothing: an EMPTY "
    "review field still counts as a review, while sending nothing "
    "is recorded as no review at all."
)

# 2026-08-03 (Fix 2): when the duplicate review is ON, the EMPTY finalize is
# precisely the call that FORFEITS it -- so labelling that call "recommended"
# steered hosts into discarding a review the tester had switched on. Observed on
# run3 (TICKET-5645): 8 mutually blind categories, 98 cases,
# QA_HOST_DEDUP_REVIEW_ENABLED=true, and zero cross-category review, because the
# host took the route this text recommended. Recommend the SIDECAR instead and
# name the empty form as the forfeiting alternative. The review FIELD is still
# never named here -- that is taught once at prepare time, pinned by
# test_submit_category_is_silent_without_the_field.
_CATEGORY_PRIMARY_BULLET = (
    "- **Finalize from these rows, keeping your review "
    "(recommended)**: when every category is staged, call "
    "`qa_submit_suite` with this prep_id and the small review "
    "SIDECAR object described in your preparation instructions "
    "(no `test_cases`). No case is re-sent, nothing already "
    "staged can be lost, and the review you were asked to run is "
    "carried across the merge. Found nothing? Send the sidecar "
    "anyway with its review field EMPTY -- an EMPTY review field "
    "still counts as a review.\n"
    "- **Or finalize with an EMPTY `suite_json` "
    '(`suite_json=""`)**: the same crash-safe merge, but it '
    "FORFEITS that review.\n"
)

_CATEGORY_ROUTE_REVIEW = (
    "Choose ONE route -- do not do both:\n\n"
    f"{_CATEGORY_PRIMARY_BULLET}"
    f"- **Or send one merged `suite_json`**: the {_CATEGORY_REVIEW_LABEL} "
    "runs on it, and the rows staged here are ignored -- but "
    "nothing at all is saved until that single call, so an "
    "interrupted chat loses every category.\n\n"
    "Sending both costs a round trip and the tokens to repeat "
    "every case: a non-empty `suite_json` is authoritative, so "
    "nothing staged here is merged in."
)


def _category_route(*, review_available: bool) -> str:
    """The closing "how to finalize" block of a category reply."""
    if review_available:
        return _CATEGORY_ROUTE_REVIEW
    return (
        "When every category is in, call `qa_submit_suite` with this "
        "prep_id and " + export._finalize_route_phrase(short=True) + " -- the rows "
        "staged above are merged for you.\n\n"
        "> ⚠️  Do NOT also send a full merged `suite_json`: a "
        "non-empty one is authoritative, so every row staged here is "
        "**ignored** and re-sending the cases costs a round trip and the "
        "tokens to repeat them."
    )
