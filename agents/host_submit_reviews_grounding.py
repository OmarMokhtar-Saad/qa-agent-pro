"""Grounding review of qa_submit_suite (moved from handlers, C3a step 2b).

The entailment review's verdicts rode in on THIS submission -- no extra round
trip and no server-side LLM call -- and are UNTRUSTED, so
tools.grounding_verdicts matches every id against the submitted suite,
enum-gates the verdicts, caps the notes and refuses a batch that marks more
than 40% of the suite ungrounded. It never removes a case: an ungrounded one
is REPORTED for a human to confirm or delete, because it may be a real
requirement nobody wrote down. "" when the optional field is absent, so a
normal submit is byte-identical.

Re-file the cases that review judged ungrounded. This must happen BEFORE
_finalize_generation for exactly the reason the duplicate review does:
finalize RENUMBERS every tc_id, so anything keyed to the ids the host
submitted has to act first. The removed cases are NOT deleted -- they go to
their own workbook sheet with their own AR-nnn ids (a retained TC-nnn would
collide with an unrelated case that inherited that number), so a requirement
nobody wrote down is still in front of a human.
"""

from __future__ import annotations

import logging
from typing import Any

from agents.host_submit_ports_prep import SubmitPorts
from agents.host_submit_types import SubGround

logger = logging.getLogger("agents.host_mode")


def _refile(routing: Any, all_cases: list) -> tuple[list, list]:
    removed = {c.tc_id for c in routing.routed}
    logger.info(
        "Grounding review moved %d case(s) to the Assumed Requirements sheet",
        len(routing.routed),
    )
    return routing.rows, [c for c in all_cases if c.tc_id not in removed]


def grounding_review(parsed: Any, all_cases: list, ports: SubmitPorts) -> SubGround:
    raw = getattr(parsed, "raw_grounding_verdicts", None)
    grounding_note = ports.review.build_grounding_section(raw, all_cases)
    assumed_rows: list = []
    if grounding_note:
        routing = ports.review.route_ungrounded_cases(raw, all_cases)
        if routing is not None and routing.routed:
            assumed_rows, all_cases = _refile(routing, all_cases)
    return SubGround(grounding_note, assumed_rows, all_cases)
