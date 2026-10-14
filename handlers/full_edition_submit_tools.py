"""MCP registration adapters: full_edition_submit_tools."""

from typing import Any

from handlers import _common, _lane, explore_bug, submit_suite  # noqa: F401
from handlers import full_edition_submit_docs as _docs
from handlers.core_tools_run import tool_with_doc
from tools import settings_port


def register_full_edition_submit_tools(mcp, kit, rt) -> None:
    """Register the bug-report and exploratory submit tools (full edition only)."""
    if not _common.test_cases_only():
        _register_submit_bug_report_tool(mcp, kit, rt)
        cfg = settings_port.get()
        if cfg.qa_testrail_push_enabled or cfg.qa_xray_push_enabled:
            _register_push_suite_tool(mcp, kit, rt)
        if cfg.qa_api_test_enabled and _lane.api_lane is not None:
            _register_api_intake_tools(mcp, kit, rt)
            _register_api_suite_tools(mcp, kit, rt)
        _register_submit_explore_step_tool(mcp, kit, rt)


def _register_submit_bug_report_tool(mcp, kit, rt) -> None:
    """Register qa_submit_bug_report (the caller holds the edition gate)."""
    Context: Any = kit.context

    @tool_with_doc(mcp, _docs.SUBMIT_BUG_REPORT_DOC)
    async def qa_submit_bug_report(task_id: str, report: str, ctx: Context) -> str:
        return await rt._tracked(
            "qa_submit_bug_report",
            ctx,
            explore_bug.handle_submit_bug_report(
                task_id, report, progress=rt._make_progress(ctx)
            ),
        )


def _register_push_suite_tool(mcp, kit, rt) -> None:
    """Register qa_push_suite (the caller holds the push-flag gate)."""
    Context: Any = kit.context

    @tool_with_doc(mcp, _docs.PUSH_SUITE_DOC)
    async def qa_push_suite(  # noqa: PLR0913 - MCP tool schema
        suite_id: str,
        target: str,
        project_id: int = 0,
        section_name: str = "",
        apply: bool = False,
        ctx: Context = None,
    ) -> str:
        return await rt._tracked(
            "qa_push_suite",
            ctx,
            submit_suite.handle_push_suite(
                suite_id,
                target,
                submit_suite.PushOptions(
                    project_id, section_name, apply, rt._make_progress(ctx)
                ),
            ),
        )


def _register_api_project_tool(mcp, kit, rt) -> None:
    """Register qa_api_project."""
    Context: Any = kit.context

    @tool_with_doc(mcp, _docs.API_PROJECT_DOC)
    async def qa_api_project(
        create: str = "", use: str = "", ctx: Context = None
    ) -> str:
        return await rt._tracked_noted(
            "qa_api_project",
            ctx,
            _lane.api_lane.handle_api_project(
                create, use, progress=rt._make_progress(ctx)
            ),
        )


def _register_prepare_api_tests_tool(mcp, kit, rt) -> None:
    """Register qa_prepare_api_tests."""
    Context: Any = kit.context

    @tool_with_doc(mcp, _docs.PREPARE_API_TESTS_DOC)
    async def qa_prepare_api_tests(  # noqa: PLR0913 - MCP tool schema
        input: str = "",
        intake_id: str = "",
        confirmed: bool = False,
        project: str = "",
        ctx: Context = None,
    ) -> str:
        args = _lane.api_lane.ApiPrepareArgs(input, intake_id, confirmed, project)
        return await rt._tracked_noted(
            "qa_prepare_api_tests",
            ctx,
            _lane.api_lane.handle_prepare_api_tests(
                args, progress=rt._make_progress(ctx)
            ),
        )


def _register_api_intake_tools(mcp, kit, rt) -> None:
    """Register qa_api_project and qa_prepare_api_tests (API flag gate held by the caller)."""
    _register_api_project_tool(mcp, kit, rt)
    _register_prepare_api_tests_tool(mcp, kit, rt)


def _register_submit_api_tests_tool(mcp, kit, rt) -> None:
    """Register qa_submit_api_tests."""
    Context: Any = kit.context

    @tool_with_doc(mcp, _docs.SUBMIT_API_TESTS_DOC)
    async def qa_submit_api_tests(task_id: str, suite: str, ctx: Context) -> str:
        return await rt._tracked(
            "qa_submit_api_tests",
            ctx,
            _lane.api_lane.handle_submit_api_tests(
                task_id, suite, progress=rt._make_progress(ctx)
            ),
        )


def _register_write_api_test_tool(mcp, kit, rt) -> None:
    """Register qa_write_api_test."""
    Context: Any = kit.context

    @tool_with_doc(mcp, _docs.WRITE_API_TEST_DOC)
    async def qa_write_api_test(  # noqa: PLR0913 - MCP tool schema
        suite_id: str,
        apply: bool = False,
        project: str = "",
        ctx: Context = None,
    ) -> str:
        args = _lane.api_lane.ApiWriteArgs(suite_id, apply, project)
        return await rt._tracked_noted(
            "qa_write_api_test",
            ctx,
            _lane.api_lane.handle_write_api_test(args, progress=rt._make_progress(ctx)),
        )


def _register_api_suite_tools(mcp, kit, rt) -> None:
    """Register qa_submit_api_tests and qa_write_api_test (API flag gate held by the caller)."""
    _register_submit_api_tests_tool(mcp, kit, rt)
    _register_write_api_test_tool(mcp, kit, rt)


def _register_submit_explore_step_tool(mcp, kit, rt) -> None:
    """Register qa_submit_explore_step (the caller holds the edition gate)."""
    Context: Any = kit.context

    @tool_with_doc(mcp, _docs.SUBMIT_EXPLORE_STEP_DOC)
    async def qa_submit_explore_step(task_id: str, step: str, ctx: Context) -> str:
        return await rt._tracked(
            "qa_submit_explore_step",
            ctx,
            explore_bug.handle_submit_explore_step(
                task_id, step, progress=rt._make_progress(ctx)
            ),
        )
