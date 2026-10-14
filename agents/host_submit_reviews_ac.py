"""Acceptance-criteria review of qa_submit_suite (moved from handlers, C3a step 2b).

The AC boomerang's return field. It rode in on THIS submission -- no extra
round trip and no server-side LLM call -- and is UNTRUSTED, so
host_mode.extract_host_acs shape-validates it, re-canonicalises the ids and
strips URLs before anything reads it. Adopted into prepared.acs
(which _finalize_generation reads for the RTM) but NEVER into
prepared.source_acs: source_acs is the ground truth the AC-anchoring check
anchors against, and a model-derived criterion is not a ticket requirement.
Runs only when THIS prep shipped the job, so a normal submit is byte-identical.
"""

from __future__ import annotations

from agents.host_submit_ports_prep import SubmitPorts
from agents.host_submit_types import ReviewIn, SubAc


def ac_review(args: ReviewIn, ports: SubmitPorts) -> SubAc:
    ac_result = None
    ac_note = ""
    if args.meta.get("host_ac_job"):
        raw = getattr(args.parsed, "raw_acceptance_criteria", None)
        ac_result = ports.review.extract_host_acs(raw)
        if ac_result.ran and not getattr(args.prepared, "acs", None):
            args.prepared.acs = list(ac_result.acs)
        ac_note = ports.review.build_host_ac_section(ac_result, args.all_cases)
    return SubAc(ac_result, ac_note)
