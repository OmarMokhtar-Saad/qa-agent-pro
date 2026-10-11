"""MCP registration adapters: full_edition_submit_tools."""

from config.settings import settings
from handlers import _common, api, explore_bug, submit_suite


def register_full_edition_submit_tools(mcp, Context, rt) -> None:
    """Register the bug-report and exploratory submit tools (full edition only)."""
    if not _common._test_cases_only():
        _register_submit_bug_report_tool(mcp, Context, rt)
        if settings.qa_testrail_push_enabled or settings.qa_xray_push_enabled:
            _register_push_suite_tool(mcp, Context, rt)
        if settings.qa_api_test_enabled:
            _register_api_intake_tools(mcp, Context, rt)
            _register_api_suite_tools(mcp, Context, rt)
        _register_submit_explore_step_tool(mcp, Context, rt)


def _register_submit_bug_report_tool(mcp, Context, rt) -> None:
    """Register qa_submit_bug_report (the caller holds the edition gate)."""

    @mcp.tool()
    async def qa_submit_bug_report(task_id: str, report: str, ctx: Context) -> str:
        """Submit the bug report YOU wrote for a task opened by qa_bug_report: pass the task_id and the full markdown as `report`, written exactly as the envelope's system_prompt specifies. The server validates the required sections, saves it to the corpus, and either returns the finished report or asks you to re-emit it against a NEW task id."""
        return await rt._tracked(
            "qa_submit_bug_report",
            ctx,
            explore_bug.handle_submit_bug_report(
                task_id, report, progress=rt._make_progress(ctx)
            ),
        )


def _register_push_suite_tool(mcp, Context, rt) -> None:
    """Register qa_push_suite (the caller holds the push-flag gate)."""

    @mcp.tool()
    async def qa_push_suite(
        suite_id: str,
        target: str,
        project_id: int = 0,
        section_name: str = "",
        apply: bool = False,
        ctx: Context = None,
    ) -> str:
        """Push a stored test suite into TestRail or Xray. target="testrail" (needs the numeric project_id from the TestRail URL) or "xray". Defaults to a PREVIEW that sends nothing. A real push needs apply=true AND the target's kill-switch flag enabled in .env. Nothing here can delete the cases afterwards."""
        return await rt._tracked(
            "qa_push_suite",
            ctx,
            submit_suite.handle_push_suite(
                suite_id,
                target,
                project_id=project_id,
                section_name=section_name,
                apply=apply,
                progress=rt._make_progress(ctx),
            ),
        )


def _register_api_intake_tools(mcp, Context, rt) -> None:
    """Register qa_api_project and qa_prepare_api_tests (API flag gate held by the caller)."""

    @mcp.tool()
    async def qa_api_project(
        create: str = "", use: str = "", ctx: Context = None
    ) -> str:
        """Create a new API test project, or continue an existing one. Every API flow starts here. With NO arguments it returns the choice plus registered projects: ask the tester in plain chat. create="<name>" fetches the public template, renames it, makes ONE local commit (nothing pushed) and proves it compiles. use="<name or path>" continues an existing project. Then call qa_prepare_api_tests."""
        return await rt._tracked_noted(
            "qa_api_project",
            ctx,
            api.handle_api_project(create, use, progress=rt._make_progress(ctx)),
        )

    @mcp.tool()
    async def qa_prepare_api_tests(
        input: str = "",
        intake_id: str = "",
        confirmed: bool = False,
        project: str = "",
        ctx: Context = None,
    ) -> str:
        """Start (or continue) an API endpoint test intake. Chat-only: no model call. Paste a contract template, curl command, OpenAPI URL/JSON or prose. Returns an intake card (questions to ask) or, once complete and confirmed=true, a task envelope YOU answer, then call qa_submit_api_tests with the task_id and your cases. project="<name>" scopes the endpoint registry; pass it once, on the first call."""
        return await rt._tracked_noted(
            "qa_prepare_api_tests",
            ctx,
            api.handle_prepare_api_tests(
                input,
                intake_id,
                confirmed,
                project,
                progress=rt._make_progress(ctx),
            ),
        )


def _register_api_suite_tools(mcp, Context, rt) -> None:
    """Register qa_submit_api_tests and qa_write_api_test (API flag gate held by the caller)."""

    @mcp.tool()
    async def qa_submit_api_tests(task_id: str, suite: str, ctx: Context) -> str:
        """Submit the API test cases YOU generated for a qa_prepare_api_tests task: the task_id and your JSON {"cases": [...]}. The server grounds every assertion against the confirmed contract (dropping hallucinated fields, refusing cases that cannot fail) and returns the suite + a suite_id for qa_write_api_test."""
        return await rt._tracked(
            "qa_submit_api_tests",
            ctx,
            api.handle_submit_api_tests(
                task_id, suite, progress=rt._make_progress(ctx)
            ),
        )

    @mcp.tool()
    async def qa_write_api_test(
        suite_id: str,
        apply: bool = False,
        project: str = "",
        ctx: Context = None,
    ) -> str:
        """Render + (dry-run or) write the Java tests for a finalized suite. apply=false (default) returns the branch, target paths and Java source, writing nothing. apply=true writes via the framework repo's ops pipeline, only when QA_API_FRAMEWORK_WRITE_ENABLED is on and QA_API_FRAMEWORK_WRITE_DRY_RUN is off. Never main, never push. project="<name>" targets a qa_api_project project; omit it for QA_API_FRAMEWORK_PATH."""
        return await rt._tracked_noted(
            "qa_write_api_test",
            ctx,
            api.handle_write_api_test(
                suite_id, apply, project, progress=rt._make_progress(ctx)
            ),
        )


def _register_submit_explore_step_tool(mcp, Context, rt) -> None:
    """Register qa_submit_explore_step (the caller holds the edition gate)."""

    @mcp.tool()
    async def qa_submit_explore_step(task_id: str, step: str, ctx: Context) -> str:
        """Submit the coaching step YOU wrote for a task opened by qa_explore_step. Include the trailing <meta>area: …; phase: …</meta> line the system_prompt asks for: the server parses it to track coverage, then strips it."""
        return await rt._tracked(
            "qa_submit_explore_step",
            ctx,
            explore_bug.handle_submit_explore_step(
                task_id, step, progress=rt._make_progress(ctx)
            ),
        )
