"""MCP registration adapters: suite_tools."""

from typing import Any

from handlers import export, prep_status, submit_category, submit_suite


def register_suite_tools(mcp, kit, rt) -> None:
    """Register the submit, status, job, category and export tools."""
    _register_submit_suite_tool(mcp, kit, rt)
    _register_suite_status_tools(mcp, kit, rt)
    _register_category_and_export_tools(mcp, kit, rt)


def _register_submit_suite_tool(mcp, kit, rt) -> None:
    """Register qa_submit_suite."""
    Context: Any = kit.context

    @mcp.tool()
    async def qa_submit_suite(  # noqa: PLR0913 - MCP tool schema
        ctx: Context,
        prep_id: str = "",
        suite_json: str | dict = "",
        volume_floor_ack: bool = False,
        image_relevance_ack: bool = False,
        step_assertion_ack: bool = False,
        quality_gate_ack: bool = False,
    ) -> str:
        """Submit a host-generated suite to be validated, finalized, exported and persisted (the BACK half of host mode). Call AFTER qa_prepare_test_cases with its `prep_id` and `suite_json`: one JSON object with a merged `test_cases` array per the payload's response_schema (an object, or a JSON string).

        Path A (after qa_submit_category): pass a small review SIDECAR, a JSON object with `duplicate_groups` (empty list if none) and NO `test_cases`; suite_json="" also finalizes but forfeits this review.

        The reply is the finished suite plus the exported file path (relay it, do not ask which format), OR refusals and gaps: fix just those and call again with the SAME prep_id. The acks (volume_floor_ack, image_relevance_ack, step_assertion_ack, quality_gate_ack) work only after that refusal and only on the USER's word, never your own judgement."""
        return await rt._tracked(
            "qa_submit_suite",
            ctx,
            submit_suite.handle_submit_suite(
                prep_id,
                suite_json,
                submit_suite.SubmitOptions(
                    volume_floor_ack,
                    image_relevance_ack,
                    step_assertion_ack,
                    quality_gate_ack,
                    rt._make_asker(ctx),
                    rt._make_progress(ctx),
                ),
            ),
        )


def _register_suite_status_tools(mcp, kit, rt) -> None:
    """Register the read-only qa_prep_status and qa_get_category_job."""
    _register_prep_status_tool(mcp, kit, rt)
    _register_category_job_tool(mcp, kit, rt)


def _register_prep_status_tool(mcp, kit, rt) -> None:
    """Register qa_prep_status."""
    Context: Any = kit.context
    ToolAnnotations: Any = kit.annotations

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def qa_prep_status(ctx: Context, prep_id: str = "") -> str:
        """Show which categories are staged for a host-mode prep_id and whether the per-category (Path A) finalize is allowed yet. Use while staging with qa_submit_category. ready=yes means you may call qa_submit_suite with a small review SIDECAR carrying duplicate_groups, or suite_json="" (no review). Path B (full merged suite_json) does not need ready=yes."""
        return await rt._tracked(
            "qa_prep_status",
            ctx,
            prep_status.handle_prep_status(prep_id),
        )


def _register_category_job_tool(mcp, kit, rt) -> None:
    Context: Any = kit.context
    ToolAnnotations: Any = kit.annotations

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def qa_get_category_job(
        ctx: Context, prep_id: str = "", category_name: str = ""
    ) -> str:
        """Return ONE self-contained category generation job for a prep_id (system_prompt + user_context + instruction + response_schema). category_name should match orchestration.expected_categories; pass "all" (or "*") for EVERY job in ONE call, always preferred. Generation runs 6.5-24 minutes per category in your own chat model: tell the tester a short status line -- "Generating <category>..." -- before you start writing each category, so a long gap is never silent."""
        _cache_key = ("qa_get_category_job", prep_id, category_name)
        _cached = rt._recent_call_cached(_cache_key)
        if _cached is not None:
            return _cached
        _result = await rt._tracked(
            "qa_get_category_job",
            ctx,
            prep_status.handle_get_category_job(prep_id, category_name),
        )
        rt._recent_call_store(_cache_key, _result)
        return _result


def _export_options(rt, ctx, **keys: str) -> export.ExportOptions:
    return export.ExportOptions(
        **keys,
        choose=rt._make_chooser(ctx),
        progress=rt._make_progress(ctx),
    )


def _register_category_and_export_tools(mcp, kit, rt) -> None:
    """Register qa_submit_category and qa_export_suite."""
    Context: Any = kit.context

    @mcp.tool()
    async def qa_submit_category(  # noqa: PLR0913 - MCP tool schema
        ctx: Context,
        prep_id: str = "",
        category_name: str = "",
        suite_json: str | dict = "",
        replace_smaller: bool = False,
    ) -> str:
        """Submit ONE category's cases (Path A, recommended): stage each category as soon as it is written. Pass the `prep_id` from qa_prepare_test_cases, the category name and `suite_json` for THAT category (a JSON object, or a string). Re-submitting REPLACES that category; do not re-submit a staged one unless a reply asked (check `qa_prep_status`). Fewer cases than staged is REFUSED; replace_smaller=true only when deliberate. When all are staged, call qa_submit_suite with the same prep_id and an empty suite_json or a review SIDECAR."""
        return await rt._tracked(
            "qa_submit_category",
            ctx,
            submit_category.handle_submit_category(
                prep_id,
                category_name,
                suite_json,
                progress=rt._make_progress(ctx),
                replace_smaller=replace_smaller,
            ),
        )

    @mcp.tool()
    async def qa_export_suite(  # noqa: PLR0913 - MCP tool schema
        ctx: Context,
        suite_id: str = "",
        format: str = "",
        output_dir: str = "",
        prep_id: str = "",
    ) -> str:
        """Export a stored suite (by suite_id) to csv | xlsx | gherkin | playwright | testrail; returns the written file path. Writes files only, never pushes to a TMS. `output_dir` is optional: a FULL path (`~/Desktop`); a bare relative word is refused. `prep_id` is NOT an export key: only the suite_id `qa_submit_suite` returns is."""
        return await rt._tracked(
            "qa_export_suite",
            ctx,
            export.handle_export_suite(
                suite_id,
                format,
                _export_options(rt, ctx, output_dir=output_dir, prep_id=prep_id),
            ),
        )
