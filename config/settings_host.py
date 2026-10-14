"""Host-boomerang gate, image and dedup fields.

Plain ``BaseModel`` mixin; ``config.settings.Settings`` composes it.
"""

from __future__ import annotations

from pydantic import BaseModel


class HostSettings(BaseModel):
    # --- Refuse a host submit with no verified ambiguity preflight (ON) -----
    # QA_HOST_AMBIGUITY_REVIEW_ENABLED hands the TICKET-7154 pre-pass to the host,
    # which also removes the server's only evidence that the check happened. The
    # job now asks for an `ambiguity_result` back and the submit reply ALWAYS
    # discloses a missing or `high` verdict. This flag turns that disclosure into
    # a REFUSAL.
    #
    # DEFAULT FLIPPED TO ON, 2026-08-29 (TICKET-5692). The OFF rationale -- that a
    # refusal throws away a generation the tester already paid for -- assumed a
    # cost the refusal does not actually impose: it deletes no prep, drops no
    # staged category row and consumes no remediation round, so the tester runs
    # step 0 and resubmits the SAME suite under the SAME prep_id, at the price of
    # one round trip. Weighed against that, a disclosure buried in a reply a
    # summarising host model prunes did not stop the TICKET-5692 run finalizing 80
    # cases against a ticket nothing had checked for being too under-specified to
    # test -- which is the TICKET-7154 failure this preflight exists to prevent.
    # "No verdict" is also structurally different from "checked and found
    # nothing", and only a refusal keeps those two apart.
    # Still an `operator_choice`, and OFF remains legitimate per install: a team
    # whose tickets are written to a template, or one whose host client cannot be
    # relied on to return the field, is better served by the disclosure than by a
    # gate it will learn to route around. Setting
    # QA_HOST_AMBIGUITY_REQUIRE_RESULT=false restores the previous behaviour
    # exactly, with no code change.
    # Inert unless QA_HOST_AMBIGUITY_REVIEW_ENABLED actually shipped the job (it
    # is keyed off the prep's meta stamp, not off the flag, so a mid-flow flip
    # cannot change an in-flight prep).
    qa_host_ambiguity_require_result: bool = True

    # --- Refuse a host submit whose screens are off-topic / unjudged (OFF) --
    # Batch 4, LAYER 2 (2026-08-09). Mirrors the flag above exactly: the
    # disclosure is the honest-by-default behaviour, and an operator who
    # genuinely relies on the screens being right turns the REFUSAL on. ON, a
    # submission is refused at finalize when THIS prep asked for a relevance
    # verdict, actually forwarded screens (captured or chat-attested), and the
    # submission carries either a `relevant: "no"` verdict or NO usable verdict
    # at all.
    # `unsure` PASSES, with Batch 2's warning. Refusing on uncertainty punishes
    # the honest answer and teaches a host that `yes` is the cheap one, which
    # would silently destroy the whole signal; `no` and "nothing came back" are
    # unambiguous and are what the reported run actually produced.
    # Default OFF because it changes whether an ALREADY-GENERATED suite is
    # accepted -- the same reasoning that shipped QA_HOST_VOLUME_FLOOR_ENABLED
    # OFF -- and because the verdict is UNTRUSTED self-report derived partly
    # from attacker-influenceable pixels, so as a hard gate a hostile or
    # malformed field could refuse a perfectly good suite. The refusal is
    # cheap and reversible: the prep and every staged category row survive
    # ("Nothing was discarded"), no remediation round is consumed, and
    # `image_relevance_ack=true` on qa_submit_suite clears it on the SECOND
    # submit (two-beat, exactly like volume_floor_ack).
    # Inert unless the prep's own meta stamps say so: it is keyed off
    # `host_image_require_relevant` / `host_image_relevance` / the two image
    # counts, never off the live flag, so a mid-flow .env flip cannot change an
    # in-flight prep and an OLD envelope is untouched.
    qa_host_image_require_relevant: bool = False

    # --- Host duplicate review: flags DELETED 2026-08-12 ----------------
    # QA_HOST_CATEGORY_RESUBMIT_NOTE_ENABLED,
    # QA_HOST_CATEGORY_SHRINK_GUARD_ENABLED,
    # QA_HOST_IMAGE_DESCRIPTION_ENABLED and QA_HOST_DEDUP_REVIEW_ENABLED were
    # deleted as flags on 2026-08-12 (flag-surface reduction, batch 1): every
    # one of them had soaked ON, so their ON behaviour is now unconditional and
    # no .env value can change it. The duplicate REVIEW is always requested;
    # whether the reported groups are ACTED on is still the opt-in sub-flag
    # below. See docs/FEATURE_FLAGS.md -> "Changelog 2026-08-12".
    # Sub-flag: actually REMOVE the non-keeper members of each reported group.
    # Default OFF, and deliberately ASYMMETRIC with the semantic dedup path
    # (which does remove -- itself hardcoded OFF on 2026-08-13, flag-surface
    # reduction batch 7, retained-for-revival): that path would drop on a
    # NUMERIC cosine >= the 0.9 module constant over a fixed payload, with a
    # protected-id list and the NB-016 sole-tracer rescue. A host model's
    # free-form judgement has no threshold and no calibrated precision, and --
    # unlike a deterministic score computed server-side -- it arrives as
    # UNTRUSTED input. The realistic threat is NOT an
    # untrustworthy host model: it is injected content inside the _GUARD-wrapped
    # Jira/comment text that host mode deliberately places in the host's own
    # context. So this path is DESTRUCTIVE + attacker-influenced and is bounded by
    # the two deterministic server-side screens below, in addition to defaulting
    # OFF and to the NB-016 rescue mirrored in agents/host_*.py.
    qa_host_dedup_apply: bool = False
    # Caps on the UNTRUSTED field's SHAPE: how many groups are considered and how
    # many cases one group may name. Never-raise-coerced as positive ints and
    # additionally hard-capped in agents/host_*.py, whose constant wins when it
    # is smaller. NB these are shape caps, not a safety bound: 50 x 12 = 550
    # removable ids, so they do NOT stop a disjoint partition of the suite. The two
    # ratios below are the actual bound.
    qa_host_dedup_max_groups: int = 50
    qa_host_dedup_max_group_size: int = 12
    # SAFETY BOUND (the one that actually bounds the destructive path). Max share of
    # the SUBMITTED cases one host review may remove; above it the WHOLE review is
    # refused (nothing removed) and the refusal is reported verbatim. An operator may
    # LOWER this; the module ceiling (0.40) in agents/host_*.py wins, so it can
    # never be raised. Deliberately CORPUS-INDEPENDENT: it needs no calibration, so
    # it is a guarantee rather than a tuned guess. The second bound is a module
    # constant with no .env knob at all (_DUP_MAX_APPLY_GROUP_SIZE = 4: more than 4
    # cases in one "duplicate" cluster is a partition primitive, not a duplicate).
    qa_host_dedup_max_removal_ratio: float = 0.35
    # PRESENTATION ONLY -- not a bound. Groups whose server-measured textual
    # agreement falls below this are LABELLED "LOW, review before trusting" in the
    # reply. It gates nothing, because it cannot: MEASURED 2026-07-29, the
    # motivating cross-category duplicate scores 0.29 while two unrelated cases score
    # 0.28-0.34 and two cases that must NOT be merged score 0.95, so no threshold
    # separates the classes (see agents/host_dedup.dup_agreements for the full table).
    # Shipping an uncalibrated number as a security bound would look like a
    # guarantee and not be one.
    qa_host_dedup_low_text_ratio: float = 0.5

    # --- Host-mode parallel chat fan-out ---------------------------------------
    # UNCONDITIONAL since 2026-08-13 (flag-surface reduction, batch 8a):
    # QA_HOST_PARALLEL_FANOUT_ENABLED was DELETED and hardcoded to `True` -- the
    # SHIPPED value of the public distribution .env template, NOT this field's
    # code default. qa_prepare_test_cases therefore always carries the
    # orchestration contract that lets the PARENT chat spawn one same-session
    # worker per category (Cursor Task / equivalent), always exposes
    # qa_prep_status, and always applies the empty-suite finalize completeness
    # gate. MCP cannot invoke host Task tools, so this only ever changed the
    # prepare payload, the instructions, that status tool and that gate.
    # Primary finalize (Path A, crash-safe -- 2026-07-31 incident):
    # qa_submit_category x N as each worker returns, then an empty
    # qa_submit_suite (+ optional acceptance_criteria/ambiguity_result sidecar),
    # gated until every expected category is staged. Fallback (Path B): merge in
    # the parent + ONE full qa_submit_suite -- which keeps the dedup/coverage
    # review (it needs the merged suite's global tc_ids) but is lost wholesale if
    # the chat dies before that single call, exactly how the first live run died.
    # Fan-out is unconditional (no seam).
    # See docs/FEATURE_FLAGS.md.

    # --- Volume floor + duplicate-prepare guard: flags DELETED 2026-08-12 -
    # QA_HOST_VOLUME_FLOOR_ENABLED and QA_HOST_DUPLICATE_PREP_GUARD_ENABLED were
    # deleted as flags on 2026-08-12 (flag-surface reduction, batch 1); both had
    # soaked ON and are now unconditional. The floor is still always the prep's
    # OWN stamped value, the guard is still keyed on source_url only, both still
    # fail OPEN, and `volume_floor_ack=true` is still honoured only after a
    # refusal. The WINDOW setting below stays as a tuning knob.
    qa_host_duplicate_prep_window_s: int = 1800
