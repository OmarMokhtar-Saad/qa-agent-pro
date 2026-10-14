"""UNMIGRATED_PATHS of tools.host_llm (one home; split out of host_llm.py)."""

from __future__ import annotations

# !! READ FIRST -- CORRECTION, 2026-08-17. The rationale below is preserved as
# the design record for why each ledger row is terminal, and two classes of
# statement in it are now FALSE about this tree:
#
#   * every "graph.py / evals/ still call X" clause. Both were DELETED --
#     graph.py and router.py in P2-A, evals/ and tools/eval_runner.py in P2-B
#     (2026-08-15). Nothing outside the host route reaches any of these paths,
#     because there is no outside route left.
#   * every "QA_SERVER_LLM_ALLOW=<id> revives it after the Phase-6 flip"
#     recipe. That setting was DELETED on 2026-08-15 and there is no flip
#     pending; llm.py has had no backend at all since P2-G (2026-08-16), so
#     there is nothing an allow-list could re-enable. Reviving one of these
#     paths is a fresh implementation against the house rule in CLAUDE.md --
#     fold it into a prepare/submit boomerang as a HostJob, or open a task
#     through this module's broker.
#
# The ids themselves are unaffected: LEDGER_IDS never shrinks, and an id
# outliving its code is what keeps "this path migrated" checkable.
UNMIGRATED_PATHS: tuple[tuple[str, str], ...] = (
    # test_scenario_agent.jira_images left this tuple in the residue-cleanup R1
    # ops file that flipped its ledger row to `migrated` (ledger rule 4: same op,
    # or the drift test fails). The fold has been live since v1.10.0 -- IMAGE_JOB,
    # extract_host_image_descriptions, the two _validate_suite pops and the
    # _sidecar_keys entry -- and the host prepare passes
    # describe_images_server_side=False UNCONDITIONALLY, so no MCP route can reach
    # the server-side ask_vision. The legacy branch is NOT deleted (graph.py and
    # evals/ still call generate_test_scenarios) and is now scope-tagged, so
    # QA_SERVER_LLM_ALLOW=test_scenario_agent.jira_images revives exactly that one
    # path after the Phase-6 flip. Leaving this tuple also removes the id from the
    # qa-doctor / startup disclosure -- the standing convention for a terminal
    # row -- which is correct here: the tester-facing route makes no such call.
    # The three test_scenario_agent.* rows left this tuple in the residue-cleanup
    # R2 ops file that flipped their ledger rows to terminal statuses (ledger rule
    # 4: same op, or the drift test fails). They stay in LEDGER_IDS -- ids never
    # leave it -- so an allow-list typo on any of them is still detectable.
    #
    #   server_fanout -> `migrated`. The host performs the entire 8-category
    #   fan-out (shipped v1.10.0). handle_generate_test_cases re-routes into
    #   handle_prepare_test_cases whenever resolve_generation_mode()=="host" and
    #   _host_image_forwarding_on(), and BOTH are HARDCODED, so the re-route is
    #   unconditional and the server fan-out is unreachable from every MCP route.
    #   The coverage-critic pair (critique_coverage, critique_and_fill_gaps) is
    #   reachable only from _remediate_gaps, whose entry condition begins with
    #   `remediate` -- and the host submit passes remediate=False, which also
    #   short-circuits the _checklist_remediation branch. NOT deleted: graph.py,
    #   evals/test_eval_goldens.py and evals/test_terse_schemas_goldens.py (which
    #   calls _generate_for_category DIRECTLY) still reach it, which is exactly why
    #   the tag had to go at each CALL SITE rather than around the fan-out's
    #   asyncio.gather. QA_SERVER_LLM_ALLOW=test_scenario_agent.server_fanout
    #   revives them after the flip.
    #
    #   rewrite_vague -> `disabled (disclosed)`, NOT migrated: no host job rewrites
    #   vague steps. The deterministic FLAGGING survives untouched
    #   (quality_warning_section runs outside every host keyword), so the loss is
    #   the automatic fix, not the detection -- and R2 built the submit-reply
    #   disclosure that says so, because until then the suppression was silent and
    #   an undisclosed suppression makes `disabled (disclosed)` false.
    #
    #   markdown -> `disabled (disclosed)`. ONE id, TWO calls: the advisory
    #   coverage-gap prose (suppressed by advisory_gaps=False, now disclosed by the
    #   same block) and the whole-suite markdown fallback, which is unreachable on
    #   a host submit -- all_cases there is provably non-empty, since
    #   TestSuite.test_cases carries min_length=1, host_submission._validate_suite raises
    #   when nothing valid remains, apply_duplicate_groups returns every case when
    #   the kept set would be empty, and filter_unanchored_cases never empties. The
    #   id is deliberately NOT split in two: LEDGER_IDS is the constant 24-member
    #   denominator the "N of 24" disclosure and three tests read.
    #
    # As for every terminal row, leaving this tuple also removes these ids from the
    # qa-doctor / startup disclosure. No per-mode setup_check item was added
    # (R1's precedent): no tester-selectable mode loses anything at the flip,
    # because the host route already makes none of these calls.
    # The two VISION rows left this tuple in the residue-cleanup R3 ops file that
    # flipped their ledger rows to terminal statuses (ledger rule 4: same op, or
    # the drift test fails). They stay in LEDGER_IDS -- ids never leave it -- so
    # an allow-list typo on either is still detectable. NEITHER call is deleted,
    # which corrects the master plan's "Phase 3 -- vision residue: delete the
    # now-unreachable calls" for both of them.
    #
    #   ui_extractor.describe_via_vision -> `migrated`, and this is a DEVIATION
    #   from the parent residue plan, which had pre-committed it to
    #   `disabled (disclosed)` on the premise that _describe_via_vision is
    #   "reached by every extract_ui_elements caller that does not pass
    #   defer_vision=True". Re-derived against the tree, that set is EMPTY in
    #   production: extract_ui_elements has exactly one non-test caller
    #   (ground_and_gate), which forwards its own defer_vision; ground_and_gate
    #   has exactly two callers, handle_prepare_test_cases (defer_vision=_host_img,
    #   both conjuncts HARDCODED, so always True) and handle_generate_test_cases
    #   (omits it, but its body is unreachable behind an unconditional re-route).
    #   graph.py and evals/ never touch this module at all. Meanwhile the
    #   capability genuinely MOVED: the deferred screenshot is popped, appended to
    #   host_images as rendered_page.png and shipped through IMAGE_JOB.
    #   QA_SERVER_LLM_ALLOW=ui_extractor.describe_via_vision revives the tagged
    #   default-parameter path after the flip.
    #
    #   image_description.describe_images -> `disabled (disclosed)`, NOT migrated,
    #   because only ONE of its two callers folded. The chat-attachment caller in
    #   _prepare_generation is suppressed unconditionally on the host route and
    #   carried by IMAGE_JOB. The OTHER caller -- handle_feature_analysis's
    #   mobile / jira_mobile branch -- is live, tester-facing, and has no host
    #   analog that is not new capability: qa_feature_analysis runs on the generic
    #   tools/host_llm broker, whose envelope is text and whose tool returns a
    #   str, so raw device screens cannot become MCP image content there. R3
    #   therefore built the disclosure instead of claiming a fold: the prepare
    #   reply gains a capture-but-not-described line (with distinct wording for
    #   the kill-switch cause and the api-backend-only cause) and qa-doctor
    #   gains a per-mode item naming
    #   QA_SERVER_LLM_ALLOW=image_description.describe_images -- the 5b/5c
    #   convention for a TESTER-FACING terminal row, and the only thing that makes
    #   that token true rather than aspirational.
    # rtm.acceptance_criteria left this tuple in the same residue-cleanup R1 ops
    # file, for the same reason and with the same shape: AC_JOB is the shipped
    # precedent the whole programme cites, the host prepare passes
    # synthesize_acs=False so _run_gen_acs returns [] without calling, and the
    # surviving legacy call in tools/rtm.py is now tagged _AC_LEDGER_ID so
    # QA_SERVER_LLM_ALLOW=rtm.acceptance_criteria works after the flip.
    # atomic_checklist.decompose left this tuple in the residue-cleanup R4 ops
    # file that flipped its ledger row to `migrated` (ledger rule 4: same op, or
    # the drift test fails), and it was the LAST row -- this tuple is now EMPTY,
    # which is what makes disclosure_state()'s calm branch reachable and what
    # master-plan Phase 6 gates on.
    #
    #   R4 built the programme's ONLY genuinely new fold: CHECKLIST_JOB
    #   (agents/host_*.py, mechanism A, stage step_zero, order 30, return
    #   field `checklist_items`), shipped behind QA_HOST_CHECKLIST_REVIEW_ENABLED
    #   AND-ed with QA_ATOMIC_CHECKLIST_ENABLED, which was default-OFF at the
    #   time and was DELETED on 2026-08-14, the checklist becoming
    #   unconditional. The host
    #   decomposes the ticket in step 0d of its OWN turn, generates against that
    #   checklist, and returns it; the server re-assigns every CL-NNN id, runs
    #   the pure-Python audit_granularity over it and feeds the DETERMINISTIC
    #   Pass-3 matcher unchanged. Those two server-side checks are the
    #   counterweight that makes host authorship of the requirement set
    #   defensible rather than an over-claim, and both are disclosed.
    #
    #   TWO cross-phase gates had to be widened in the same op, because both
    #   are AND-ed with "the prep produced checklist items" -- which is False
    #   at prepare time once the decomposition is boomeranged:
    #     * handlers._nli_suppress (Phase 3b, since DELETED along
    #       with the two NLI ask_json calls in tools/rtm.py it guarded -- both
    #       gone since P2-G1).
    #     * the former host_mode facade's _coverage_instruction -- HISTORICAL, this widen no
    #       longer exists. Un-widened it would have made the whole
    #       QA_HOST_COVERAGE_REVIEW_ENABLED clause vanish from the payload,
    #       deleting the host analog that 3b's row named as the replacement
    #       for the tiers it suppressed. It was found in R4 exploration; no
    #       earlier plan had recorded it. On 2026-08-12 (flag-surface
    #       reduction, batch 2b) that helper AND that flag were DELETED as an
    #       unvalidated, never-shipped experiment, so there is no host analog
    #       to keep alive: the 3b disclosure now states plainly that there is
    #       none, and _nli_suppress above is gone with the matcher it
    #       guarded, so nothing widens on this path anymore.
    #
    #
    # (The per-row rationale continues in host_llm_ledger.py, above LEDGER_IDS.)
)
