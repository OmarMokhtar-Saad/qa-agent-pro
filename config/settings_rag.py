"""RAG corpus grounding and checklist-pipeline fields.

Plain ``BaseModel`` mixin; ``config.settings.Settings`` composes it.
"""

from __future__ import annotations

from pydantic import BaseModel


class RagSettings(BaseModel):
    # RAG corpus grounding: retrieval is always attempted (there is no enable
    # flag); an empty corpus is a no-op. The knobs below are the surviving
    # controls. Retired-flag history: docs/DECISIONS.md.
    qa_rag_storage_path: str = "corpus"
    qa_rag_similarity_threshold: float = 0.3
    qa_rag_top_k: int = 5
    # Corpus similarity metric: "jaccard" (default, set overlap), "cosine"
    # (TF-IDF cosine — weights rare/discriminative terms) or "bm25" (Okapi
    # BM25, the consensus lexical-retrieval baseline; saturation-normalized
    # to [0,1) so the threshold above applies unchanged). (I-051)
    qa_rag_similarity_mode: str = "jaccard"
    # Freshness boost: entries decay with this half-life (days) and fresh ones
    # get up to +15% score. 0 disables (default — no behavior change).
    qa_rag_recency_half_life_days: int = 0
    # Corpus size cap per file: adding beyond it prunes the oldest entries.
    # 0 = unlimited (default).
    qa_rag_max_entries: int = 0

    # QA_SEMANTIC_DEDUP_THRESHOLD stood here until 2026-08-30 (audit G3). It was
    # the cosine cut-off for intra-suite semantic dedup -- a path hardcoded OFF
    # on 2026-08-13, so the field's only reader sat behind
    # agents.test_scenario_agent.semantic_dedup_enabled(), the False constant,
    # and no value an operator set could change anything. The retained-for-
    # revival _semantic_dedupe_cases path now uses a module constant of the same
    # 0.9. A stale line in an existing .env is IGNORED, not an error
    # (model_config uses extra="ignore"), pinned in tests/test_settings.py.
    # Intra-suite semantic dedup -- REMOVED as a setting 2026-08-13
    # (flag-surface reduction, batch 7 (needs-config)):
    # QA_SEMANTIC_DEDUP_ENABLED was DELETED and hardcoded OFF, so
    # agents.test_scenario_agent.semantic_dedup_enabled() returns the False
    # constant and the generation pipeline never merges cases on similarity.
    # OFF is also the SAFE direction: this was the one gate in the batch whose
    # ON state DROPS generated cases. The threshold that stood above it was
    # deleted on 2026-08-30; the revival path keeps its 0.9 as a module
    # constant.

    # --- Atomic Requirements Checklist (Batch 2) --------------------------
    # QA_ATOMIC_CHECKLIST_ENABLED was DELETED on 2026-08-14 (flag-surface
    # reduction, batch 8b-ii) and the behaviour hardcoded **ON**. Unlike every
    # other flag in batches 8b-i/8b-ii this DOES change shipped behaviour:
    # the field defaulted False, .env.example shipped `false` and the dist
    # template never carried the key, so the checklist was OFF on every
    # install. It is now unconditional -- the flag policy's "promoted to
    # default behaviour (flag deleted)" exit for an experiment. The pipeline:
    #   Pass 1  tools/atomic_checklist.decompose_to_checklist -> an unbounded,
    #           EARS-shaped, source-tagged checklist of every independently-
    #           verifiable outcome. On the (unconditional) host route this is
    #           CHECKLIST_JOB / step 0d, so it costs this server NO LLM call.
    #   Pass 2  the 8-category fan-out with that checklist injected as its OWN
    #           untrusted, CLUSTERED block (constraint-decay mitigation).
    #   Pass 3  tools/rtm.rtm_trace -> deterministic requirement_id
    #           traceability: a generated case counts as tracing a
    #           requirement only by naming its requirement_id (no
    #           similarity scoring, no matcher). A requirement_id with no
    #           tracing case is reported as an orphaned requirement.
    # The named seam is tools/atomic_checklist.checklist_enabled(), a True
    # constant, and the test suite pins it False suite-wide -- unpinned,
    # every generation test would decompose and make a real ask_json call.
    # See docs/FEATURE_FLAGS.md.
    # QA_CHECKLIST_REMEDIATION_ENABLED was DELETED on 2026-08-14
    # (flag-surface reduction, batch 8b) and the behaviour hardcoded OFF --
    # this field's own code default, and the value every shipped template
    # carried. ON it made the bounded critic loop's stop condition "every
    # checklist item is traced" instead of "the LLM critic ran out of
    # patience"; its registry rationale said plainly that it needs a tally
    # proven reliable first, and that measurement was never made. OFF the
    # legacy critic remains the stop condition, which is the state the whole
    # suite is written against. NOTHING is deleted: the checklist-driven
    # branch is retained and
    # agents.test_scenario_agent.checklist_remediation_enabled() is the named
    # seam -- a revival is one line there. See docs/FEATURE_FLAGS.md.
    # Phase-0 granularity gate. Below this the decomposition is reported as
    # probably inflated / under-split -- ADVISORY only, it never blocks
    # generation (house rule: log and degrade).
    qa_checklist_min_granularity: float = 0.6
    # Hard caps: decomposed items (anti-inflation) and the injected prompt
    # block. MAX_PROMPT_CHARS is DERIVED FROM MAX_ITEMS: a rendered line
    # ("- CL-017 [event_driven] When the user taps cancel, the system shall
    # redirect the user to the Appointment Card screen.") is ~120 chars, so
    # 200 items need ~24,000; 32,000 leaves headroom. A smaller cap would make
    # the tool truncate its
    # OWN prompt and then score the truncated items as coverage gaps; that is
    # now impossible (format_checklist_prompt_block reports exactly which ids
    # it presented and the matcher excludes the rest), but the default must
    # still fit a full checklist so the NOT-PRESENTED bucket stays empty in
    # normal operation.
    qa_checklist_max_items: int = 200
    qa_checklist_max_prompt_chars: int = 32000
