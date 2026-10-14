"""Host mode orchestration: the staged-category (step 3) and finalize (steps 4-6) instruction prose."""

# --------------------------------------------------------------------------- #
# Host STAGED CATEGORY submission (the `orchestration` / `jobs` contract)
#
# D3, 2026-08-21 -- THE PARALLEL FAN-OUT ASK IS RETIRED. This block used to open
# "PARALLEL FAN-OUT -- DECIDE THIS BEFORE YOU GENERATE ANYTHING" and tell the
# host to launch one same-session worker per category. It was ignored on THREE
# measured runs (v1.36.0, run3/TICKET-5645, TICKET-5646 on 2026-08-21), and the
# prose lever is spent: the 2026-08-03 prominence fix moved it from 61% to 47%
# of the way through `instructions` and changed nothing, so a third re-word was
# refused. What actually killed the feature is that its premise stopped being
# true:
#   * LATENCY is no longer the argument. TICKET-5646 generated 96 cases
#     SEQUENTIALLY in 2m17s, not the 26 minutes this block used to cite.
#   * CONSISTENCY argues the other way. The DF03/DF04 split that made D2 was
#     inherited from the ticket's own identifier collision; eight independent
#     workers each inherit it too, with no shared context to converge on.
#   * D4 (cross-category duplication) gets WORSE with mutually blind workers.
# The brief's own suggestion -- a `jobs_to_run` entry for the fan-out DECISION
# -- was rejected: such a job has no verifiable return artifact, so the server
# cannot tell "I decided not to fan out" from "I ignored it". That is precisely
# the unenforceable, zero-feedback delegated check the _AMBIGUITY_RETURN_CLAUSE
# comment below was written to end.
#
# WHAT IS KEPT, and why it has nothing to do with parallelism: the staged route
# (qa_submit_category per category, then qa_prep_status, then a finalize)
# carries TWO values a single merged submit does not.
#   1. CRASH-SAFETY: staged rows survive a chat reload; on the merge-in-parent
#      route nothing is saved until one final call (the 2026-07-31 TICKET-5645
#      loss). The completeness gate in mcp_handlers uses meta.expected_categories
#      stamped at prepare time.
#   2. The server-side duplicate PRESCREEN (mcp_handlers.dup_shortlist_note)
#      runs ONLY on the category path, gated on the submission completing the
#      expected set. It is the one existing hook for the D4 fix.
# So the two routes are peers with different costs, not "finalize" and
# "ALTERNATIVE finalize"; the staged one is RECOMMENDED for those two reasons
# and neither of them is speed. The duplicate review reaches the server on
# EITHER route, via the sidecar that _review_sidecar / _remap_dup_groups handle.
# Never duplicate full user_context into jobs[] (token bomb).
# Every helper below is pure / sync / never-raise where noted.
# --------------------------------------------------------------------------- #

# Fix 2 (2026-08-03): step 3's finalize sentence has to differ by flag, but this
# is ONE module-level constant and it already contains JSON braces, so str.format
# is not usable on it. Substitute a sentinel in _parallel_instruction() instead.
# Defined BEFORE the constant on purpose -- module-level constants evaluate in
# order, so referencing it from inside the constant below requires it to exist.
# WHY it must differ: with the duplicate review ON, `suite_json=""` is the call
# that FORFEITS it, and this instruction is the FIRST and most authoritative text
# the host reads. run3 (TICKET-5645) took the empty route it led with and lost the
# review across 98 cases from 8 mutually blind workers.
_FINALIZE_SENTINEL = "@@FINALIZE_ROUTE@@"

_FINALIZE_SIDECAR_FIRST = (
    "When ready=true, finalize with `qa_submit_suite` and a small JSON SIDECAR "
    "holding just `duplicate_groups` and/or `acceptance_criteria` / "
    "`ambiguity_result` and NO test_cases (the server remaps a sidecar's tc_ids "
    "across the merge) -- this KEEPS the duplicate review you were asked to run. "
    'Finalizing with suite_json="" also works and is equally crash-safe, but it '
    "FORFEITS that review. A review that found NO duplicates is still a "
    'review: report it with an EMPTY list -- `"duplicate_groups": []` -- in '
    "the sidecar, not by sending nothing."
)

_HOST_STAGED_INSTRUCTION = (
    "\n"
    "3. FETCH THE CATEGORY PACKETS IN ONE CALL. "
    '`qa_get_category_job(prep_id, "all")` returns EVERY job packet in ONE '
    "call, with the shared prompt blocks hoisted once; a single category_name "
    "returns one packet. NEVER fetch packets one call per category -- an "
    "observed run spent 8 round trips on that. Keep prep_id, system_prompt, "
    "user_context and response_schema from THIS payload; do not rely on "
    "`jobs[]` for user_context. `orchestration.expected_categories` is the "
    "exact set this server expects to see staged, and it REFUSES an incomplete "
    "staged finalize, so do not finalize early.\n"
)

# Steps 4-6: generate, then the two finalize routes. ALWAYS emitted -- a host
# with no orchestration contract still needs them -- which is why they live
# outside the seam-gated block above. _FINALIZE_SENTINEL is resolved by
# _finalize_instruction() so step 5 recommends the route that KEEPS the
# duplicate review whenever that review is enabled (Fix 2, 2026-08-03).
_HOST_FINALIZE_INSTRUCTIONS = (
    "4. For EACH of the entries in `categories`, produce test cases using "
    "`system_prompt` as your system instruction, `user_context` as the feature "
    "material, and that entry's `instruction` (its FOCUS, case-count range and "
    "preferred type). Emit ONLY a JSON object conforming to `response_schema`.\n"
    "   Set each case's `category` field to that entry's `name`, copied EXACTLY "
    '(e.g. "Positive / Happy Path"). It is what makes the exported Category '
    "column meaningful; a value the server cannot resolve is stored empty rather "
    "than guessed.\n"
    "5. SUBMIT EACH CATEGORY AS YOU FINISH IT (Path A -- recommended). Call "
    "`qa_submit_category` with this `prep_id`, the category's name and that "
    "category's JSON the moment its cases are written, before you start the "
    "next category. Pass `suite_json` STRAIGHT THROUGH as a JSON OBJECT: do NOT "
    "serialise it into a string, and do NOT write it to a file or build a "
    "script to assemble it -- measured on real payloads, a 20 KB category "
    "object and a 150 KB merged 80-case object both arrive byte-identical, and "
    "observed runs spent minutes re-encoding payloads that would have "
    "transferred as-is. A JSON string is still accepted if your client "
    "genuinely cannot send an object. Two things make this the recommended "
    "route, and NEITHER is speed: staged categories survive a chat reload or a "
    "crash and `qa_prep_status` shows what is still outstanding, while on the "
    "other route nothing is saved until the final call; and the server runs its "
    "own duplicate PRESCREEN across the staged set, which the other route never "
    "sees. "
    + _FINALIZE_SENTINEL
    + " Do not finalize early -- the server rejects an incomplete staged "
    "finalize when this orchestration was requested.\n"
    "6. OR SUBMIT THE WHOLE SUITE AT ONCE (Path B). Merge all categories into "
    "ONE JSON object with a single `test_cases` array, keeping tc_id values "
    "unique (TC-001, TC-002, ...; they are renumbered on submission), then call "
    "`qa_submit_suite` with the `prep_id` returned alongside this payload. This "
    "is a supported route, not a shortcut: take it when your client cannot hold "
    "a multi-call session. What it costs you is the two things named in step "
    "5.\n"
    "   Either way the server validates the suite, scores requirement coverage "
    "deterministically, and returns either a gap report to fix and resubmit "
    "(same prep_id) or the finished suite and its export path. If you already "
    "sent categories one at a time with `qa_submit_category`, do NOT also send "
    'the merged JSON: finalize with an EMPTY `suite_json` (`suite_json=""`) or '
    "the review sidecar described below -- a non-empty `suite_json` is "
    "authoritative, so every staged row would be ignored."
)
