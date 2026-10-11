"""MCP registration adapters: core_tools."""

from handlers import generate_prepare, jira, wizard


def register_core_tools(mcp, Context, ContentBlock, rt) -> None:
    """Register the test-case workflow tools every edition ships."""
    _register_generate_tool(mcp, Context, rt)
    _register_stage_jira_tool(mcp, Context, rt)
    _register_prepare_tool(mcp, Context, ContentBlock, rt)


def _register_generate_tool(mcp, Context, rt) -> None:
    """Register qa_generate_test_cases."""

    @mcp.tool()
    async def qa_generate_test_cases(
        ctx: Context,
        feature_or_url: str = "",
        proceed_anyway: bool = False,
        jira_content_json: str = "",
        stage_token: str = "",
        source_plan: str = "",
        attached_image_count: int = 0,
        capture_ids: list[str] | None = None,
        image_gate_ack: bool = False,
        image_carry_ack: bool = False,
    ) -> str:
        """Generate a structured test suite. feature_or_url: a description, Jira/issue URL, web page URL or Swagger/OpenAPI URL; omit it if the user did not say, the server asks.

        Returns a PREPARE PAYLOAD: YOU write the cases. Continue exactly as for qa_prepare_test_cases (qa_get_category_job(prep_id, "all") once, qa_submit_category per category, qa_submit_suite to finalize). Relay the .xlsx path from that reply; do NOT ask which export format or offer to push.

        Jira URL: a DIRECTIVE first; fetch with your own mcp__atlassian__getJiraIssue, `qa_stage_jira` each result, call again with the SAME `stage_token` (or `jira_content_json` as a JSON STRING).

        IMAGE GATE. ASK FIRST: for a Jira URL, ask the USER where the ticket's screens come from before your first call and pass `source_plan`; never guess it, and never send image_gate_ack=true unless the user explicitly said the screens do not matter."""
        _params = {
            "feature_or_url": feature_or_url,
            "proceed_anyway": proceed_anyway,
            "jira_content_json": jira_content_json,
            "stage_token": stage_token,
            "source_plan": source_plan,
            "attached_image_count": attached_image_count,
            "capture_ids": list(capture_ids or []),
            "image_gate_ack": image_gate_ack,
            "image_carry_ack": image_carry_ack,
        }
        _cache_key = rt._generate_cache_key(_params)
        _cached = rt._recent_call_cached(_cache_key, rt._GENERATE_DUP_WINDOW_S)
        if _cached is not None:
            return rt._DUP_REPLY_PREFIX + _cached
        _result = await rt._tracked(
            "qa_generate_test_cases",
            ctx,
            rt._prepare_call(wizard.handle_generate_test_cases, ctx, _params),
        )
        # Only a reply that created a prep: replaying a clarify or a gate
        # block would hide a changed tester answer (e.g. the P0-3 dialog).
        if "**prep_id:** `" in _result:
            rt._recent_call_store(_cache_key, _result)
        return _result


def _register_stage_jira_tool(mcp, Context, rt) -> None:
    """Register qa_stage_jira."""

    @mcp.tool()
    async def qa_stage_jira(
        ctx: Context, stage_token: str, part: str, json: str
    ) -> str:
        """Stage ONE raw Jira fetch result right after you fetch it. Call it after each getJiraIssue/JQL call the qa_prepare_test_cases/qa_generate_test_cases directive asked for: part='issue', 'parent' or 'siblings'. `json` is that ONE result, stringified (json.dumps(result)), under ~20KB. When every part is staged, call qa_prepare_test_cases again with the SAME feature_or_url and `stage_token`, not jira_content_json."""
        return await rt._tracked(
            "qa_stage_jira",
            ctx,
            jira.handle_stage_jira(stage_token, part, json),
        )


def _register_prepare_tool(mcp, Context, ContentBlock, rt) -> None:
    """Register qa_prepare_test_cases."""

    @mcp.tool()
    async def qa_prepare_test_cases(
        ctx: Context,
        feature_or_url: str = "",
        proceed_anyway: bool = False,
        jira_content_json: str = "",
        stage_token: str = "",
        source_plan: str = "",
        attached_image_count: int = 0,
        capture_ids: list[str] | None = None,
        image_gate_ack: bool = False,
        image_carry_ack: bool = False,
    ) -> list[ContentBlock]:
        """HOST-MODE generation: returns a grounded generation payload for YOU, the host model, to run; the server calls no LLM. Call it first for any test-case request.

        feature_or_url: a description, Jira/issue URL, web page URL or Swagger/OpenAPI URL. A Jira URL first returns a DIRECTIVE: fetch with your own `mcp__atlassian__getJiraIssue`, `qa_stage_jira` each raw result, then call again with the SAME feature_or_url and `stage_token` (or `jira_content_json` as a JSON STRING, json.dumps(result)).

        With `orchestration` in the payload: generate each category, stage it with `qa_submit_category`, then `qa_prep_status`, then `qa_submit_suite`. Otherwise generate the full suite and call `qa_submit_suite`.

        IMAGE GATE. ASK FIRST: for a Jira URL, ask the USER where the ticket's screens come from before your first call and pass `source_plan` (jira, jira_attach, jira_device, jira_both, device); never guess it, and never send image_gate_ack=true unless the user explicitly said the screens do not matter.

        Under-specified ticket: relay the questions, or pass proceed_anyway=true. Each prepared (non-clarify) reply carries the standing rules."""
        result = await rt._tracked(
            "qa_prepare_test_cases",
            ctx,
            generate_prepare.handle_prepare_test_cases(
                feature_or_url,
                proceed_anyway=proceed_anyway,
                **rt._make_elicitors(ctx),
                progress=rt._make_progress(ctx),
                jira_content_json=jira_content_json,
                stage_token=stage_token,
                source_plan=source_plan,
                attached_image_count=attached_image_count,
                capture_ids=list(capture_ids or []),
                image_gate_ack=image_gate_ack,
                image_carry_ack=image_carry_ack,
            ),
        )
        return rt._prepare_payload_to_content(result)
