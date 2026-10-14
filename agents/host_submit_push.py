"""qa_push_suite use case (moved from handlers, C3a step 5).

This is the ONLY credentialed outbound WRITE in the tree, which is why it has
two independent guards: the target's kill-switch setting AND ``apply=true`` on
the call. Either one missing means no request is made.

A dry run (the default) needs no flag HERE: it reports what WOULD be created
and makes no request at all. The kill-switch nonetheless gates tool
REGISTRATION in ``mcp_server``, so with both push flags OFF this tool is not
offered at all and the flagless preview is reachable only once one of them is
ON. A flag-OFF call with ``apply=true`` REFUSES BY NAME rather than quietly
downgrading to a dry run -- a tester who asked to push and got a success-shaped
reply would believe cases exist in a tracker that do not.

Nothing here deletes a remote case afterwards, so a successful push says so.

The two pushers are OPTIONAL module-level imports: the public distribution
ships the test-case pipeline only and copies neither pusher
(tests/test_build_dist_host_mode_closure.py DIST_EXCLUDED), so only their own
absence reads as ``None`` and the push functions then fail by name. Tests patch
``push_suite`` on the pusher modules, which a call-time attribute lookup sees.
"""

from __future__ import annotations

import logging
from types import ModuleType
from typing import Any, NamedTuple, cast

from agents.host_submit_ports_prep import SubmitPorts
from agents.host_submit_push_text import (
    PushFlag,
    PushRequest,
    PushView,
    push_suite_refusal,
    push_suite_reply,
)
from tools.untrusted import single_line as _safe

logger = logging.getLogger("agents.host_mode")

try:
    import tools.xray_pusher as xray_pusher
except ModuleNotFoundError as _exc:
    if _exc.name != "tools.xray_pusher":
        raise
    xray_pusher = cast("Any", None)
try:
    import tools.testrail_pusher as testrail_pusher
except ModuleNotFoundError as _exc:
    if _exc.name != "tools.testrail_pusher":
        raise
    testrail_pusher = cast("Any", None)


def _pusher(mod: ModuleType | None, name: str) -> ModuleType:
    """The pusher module, or the same ImportError the old call-time import raised."""
    if mod is None:
        raise ModuleNotFoundError(f"tools.{name} is not shipped", name=f"tools.{name}")
    return mod


class PushCall(NamedTuple):
    suite_id: str
    target: str
    project_id: int
    section_name: str
    apply: bool
    progress: Any


def _push_flag(target: str, ports: SubmitPorts) -> PushFlag:
    settings = ports.io.settings
    if target == "testrail":
        return PushFlag(
            "QA_TESTRAIL_PUSH_ENABLED", bool(settings.qa_testrail_push_enabled)
        )
    return PushFlag("QA_XRAY_PUSH_ENABLED", bool(settings.qa_xray_push_enabled))


async def run_push_suite(call: PushCall, ports: SubmitPorts) -> str:
    """PUSH a stored suite into TestRail or Xray. Never raises."""
    target = (call.target or "").strip().lower()
    flag = _push_flag(target, ports)
    refusal = push_suite_refusal(target, call.apply, flag)
    if refusal:
        return refusal
    suite_id = (call.suite_id or "").strip()
    if not suite_id:
        return await ports.elicit.recent_suites_markdown("qa_push_suite")
    req = PushRequest(
        suite_id, target, call.apply, call.project_id, call.section_name, call.progress
    )
    try:
        return await _push_suite_run(req, flag, ports)
    except Exception as exc:  # never-raise contract
        logger.exception("mcp push_suite failed")
        return "⚠️ Push to " + target + " failed: " + _safe(str(exc), 200)


async def _load_suite(req: PushRequest, ports: SubmitPorts) -> Any:
    """The stored suite, or a ready reply string when it cannot be used."""
    loaded = await ports.io.load_suite(req.suite_id)
    if loaded.get("error"):
        return (
            "⚠️ Could not load suite `"
            + _safe(req.suite_id, 80)
            + "`: "
            + _safe(loaded["error"], 300)
        )
    suite = loaded.get("content")
    if suite is None:
        # F21: same two facts as qa_export_suite, said apart.
        return ports.reply.suite_unreadable_reply(req.suite_id, loaded)
    return suite


async def _audit_push(req: PushRequest, content: dict, ports: SubmitPorts) -> None:
    await ports.common.audit(
        "mcp_push_suite",
        entity_id=req.suite_id,
        detail={
            "target": req.target,
            "apply": bool(req.apply),
            "pushed": int(content.get("pushed") or 0),
            "skipped": int(content.get("skipped") or 0),
            "would_push": int(content.get("would_push") or 0),
        },
    )


async def _push_suite_run(req: PushRequest, flag: PushFlag, ports: SubmitPorts) -> str:
    """Load the suite, push it, audit it and render the reply (may raise)."""
    suite = await _load_suite(req, ports)
    if isinstance(suite, str):
        return suite
    total = len(getattr(suite, "test_cases", []) or [])
    result = await _push_suite_cases(suite, req, ports)
    if isinstance(result, str):
        return result
    if result.get("error"):
        return "⚠️ " + _safe(result["error"], 400)
    content = result.get("content") or {}
    await _audit_push(req, content, ports)
    return push_suite_reply(content, PushView(req, flag, total))


async def _push_suite_cases(
    suite: Any, req: PushRequest, ports: SubmitPorts
) -> "dict | str":
    """Run the pusher for ``req.target``; a str return is a ready reply, not a result."""
    if req.target != "testrail":
        verb = "Pushing" if req.apply else "Previewing"
        await ports.common.emit(req.progress, "\U0001f4e4 " + verb + " to Xray…")
        return await _pusher(xray_pusher, "xray_pusher").push_suite(
            suite, dry_run=not req.apply
        )
    return await _push_testrail(suite, req, ports)


def _testrail_project_id(raw: Any) -> int:
    try:
        return int(raw or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _project_id_missing(suite_id: str) -> str:
    return (
        "⚠️ TestRail needs the numeric project id: "
        '`qa_push_suite(suite_id="'
        + _safe(suite_id, 80)
        + '", target="testrail", project_id=<id>)`. '
        "It is the number in the TestRail URL for that project."
    )


async def _push_testrail(
    suite: Any, req: PushRequest, ports: SubmitPorts
) -> "dict | str":
    project_id = _testrail_project_id(req.project_id)
    if project_id <= 0:
        return _project_id_missing(req.suite_id)
    pusher = _pusher(testrail_pusher, "testrail_pusher")
    verb = "Pushing" if req.apply else "Previewing"
    await ports.common.emit(req.progress, "\U0001f4e4 " + verb + " to TestRail…")
    target = pusher.PushTarget(
        project_id, section_name=(req.section_name or "QA Assistant Export")
    )
    return await pusher.push_suite(suite, target, dry_run=not req.apply)
