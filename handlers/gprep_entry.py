"""The MCP entry of PREPARE: argument capture, the two halves, failure replies."""

from __future__ import annotations

import logging

from agents.host_prep_serde import PrepSerdeError
from handlers import _common, _elicit, capture, generate
from handlers.gprep_args import _PrepArgs
from handlers.gprep_front import _prepare_front
from handlers.gprep_run import _prepare_generate
from tools import dispatch_guard

logger = logging.getLogger("tools.mcp_handlers")


def _args_from(vals: dict) -> tuple[_PrepArgs, str]:
    """The ``_PrepArgs`` and the stage token out of the handler's own locals."""
    vals = dict(vals)
    stage_token = vals.pop("stage_token")
    text = (vals.pop("feature_or_url") or "").strip()
    return _PrepArgs(text=text, **vals), stage_token


def _clarify(msg: str, front) -> generate.PreparePayloadResult:
    """A clarify reply carrying ``msg`` and the capture retry hint."""
    hint = capture._capture_retry_hint(front.args.capture_ids)
    return generate.PreparePayloadResult(clarify=msg + hint)


async def _prepare_generate_guarded(front) -> generate.PreparePayloadResult:
    """_prepare_generate with its failures turned into a clarify reply."""
    try:
        return await _prepare_generate(front)
    except PrepSerdeError as exc:
        logger.warning("host-mode prepare serialization failed", exc_info=True)
        return _clarify(f"⚠️ Could not prepare host-mode generation: {exc}", front)
    except Exception as exc:
        logger.exception("handle_prepare_test_cases failed")
        _common.capture_error(exc, "qa_prepare_test_cases")
        return _clarify(f"⚠️ Preparation failed: {exc}", front)


async def _prepare_run(
    args: _PrepArgs, stage_token: str
) -> generate.PreparePayloadResult:
    """Run the front half, then the generation half when the front did not reply."""
    front = await _prepare_front(args, stage_token)
    if isinstance(front, generate.PreparePayloadResult):
        return front
    return await _prepare_generate_guarded(front)


# FRONT half of host-mode generation. Grounds + gates the inputs exactly
# like the server path, runs prepare_generation, serializes the result into
# the prep store, and returns the grounded payload the tester's own chat model
# runs the fan-out against. Never raises.
# The body must define no locals before _args_from(locals()): it maps exactly
# the parameters onto _PrepArgs.
async def handle_prepare_test_cases(  # noqa: PLR0913 - MCP tool schema
    feature_or_url: str,
    *,
    attached_images: list | None = None,
    proceed_anyway: bool = False,
    source_plan: str = "",
    attached_image_count: int = 0,
    capture_ids: list | None = None,
    image_gate_ack: bool = False,
    image_carry_ack: bool = False,
    choose: _common.ChooseCb = None,
    ask_text: _elicit.AskCb = None,
    progress: _common.ProgressCb = None,
    jira_content_json: str = "",
    stage_token: str = "",
) -> generate.PreparePayloadResult:
    """FRONT half of host-mode generation; grounds, gates and never raises."""
    dispatch_guard.require_dispatched("qa_prepare_test_cases")
    return await _prepare_run(*_args_from(locals()))
