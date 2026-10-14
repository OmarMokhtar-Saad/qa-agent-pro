"""Public submit/push handlers and their option objects (leaf of ``handlers.submit_suite``)."""

from __future__ import annotations

import logging
from typing import NamedTuple

from agents.host_submit_pipeline_run import SubmitCall
from agents.host_submit_push import PushCall
from agents.host_submit_types import SubAcks
from handlers import _common, _elicit
from handlers.submit_suite_cports import _ports
from handlers.submit_suite_host import host

logger = logging.getLogger("tools.mcp_handlers")


class SubmitOptions(NamedTuple):
    """Optional acknowledgements and callbacks of ``handle_submit_suite``."""

    volume_floor_ack: bool = False
    image_relevance_ack: bool = False
    step_assertion_ack: bool = False
    quality_gate_ack: bool = False
    ask_text: _elicit.AskCb = None
    progress: _common.ProgressCb = None


class PushOptions(NamedTuple):
    """Optional arguments of ``handle_push_suite``."""

    project_id: int = 0
    section_name: str = ""
    apply: bool = False
    progress: _common.ProgressCb = None


async def handle_submit_suite(
    prep_id: str,
    suite_json,
    options: SubmitOptions | None = None,
) -> str:
    """BACK half of host-mode generation (see agents.host_submit_pipeline)."""
    h = host()
    opts = options or SubmitOptions()
    h.dispatch_guard.require_dispatched("qa_submit_suite")
    prep_id = h.safe_prep_id(prep_id)  # UNTRUSTED: echoed in replies
    acks = SubAcks(
        opts.volume_floor_ack,
        opts.image_relevance_ack,
        opts.step_assertion_ack,
        opts.quality_gate_ack,
    )
    call = SubmitCall(prep_id, suite_json, acks, (opts.ask_text, opts.progress))
    return await h._guarded_submit(call)


async def _guarded_submit(call: SubmitCall) -> str:
    """Refuse a missing prep_id, else run the pipeline; never raises."""
    if not call.prep_id:
        return (
            "⚠️ Missing prep_id. Run `qa_prepare_test_cases` first, then "
            "submit with the prep_id it returned."
        )
    h = host()
    try:
        return await h.run_submit_suite(call, _ports())
    except Exception as exc:
        logger.exception("handle_submit_suite failed")
        h._common.capture_error(exc, "qa_submit_suite")
        return f"⚠️ Submitting the suite failed: {exc}"


# moved to agents.host_submit_push{,_text} (C3a step 5). The tool seam wraps it
# and a test patches it by name.
async def handle_push_suite(
    suite_id: str,
    target: str,
    options: PushOptions | None = None,
) -> str:
    """PUSH a stored suite into TestRail or Xray. Never raises.

    The only credentialed outbound WRITE: needs the target's kill-switch flag AND
    ``apply=true``; a flag-OFF real push refuses by name (see host_submit_push).
    """
    opts = options or PushOptions()
    call = PushCall(
        suite_id,
        target,
        opts.project_id,
        opts.section_name,
        opts.apply,
        opts.progress,
    )
    return await host().run_push_suite(call, _ports())
