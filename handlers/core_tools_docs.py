"""Tool descriptions of the core and corpus MCP wrappers, attached as ``__doc__``."""

# The description of `qa_generate_test_cases`, verbatim (continuation lines keep their indent).
GENERATE_DOC = """Generate a structured test suite. feature_or_url: a description, Jira/issue URL, web page URL or Swagger/OpenAPI URL; omit it if the user did not say, the server asks.

        Returns a PREPARE PAYLOAD: YOU write the cases. Continue exactly as for qa_prepare_test_cases (qa_get_category_job(prep_id, "all") once, qa_submit_category per category, qa_submit_suite to finalize). Relay the .xlsx path from that reply; do NOT ask which export format or offer to push.

        Jira URL: a DIRECTIVE first; fetch with your own mcp__atlassian__getJiraIssue, `qa_stage_jira` each result, call again with the SAME `stage_token` (or `jira_content_json` as a JSON STRING).

        IMAGE GATE. ASK FIRST: for a Jira URL, ask the USER where the ticket's screens come from before your first call and pass `source_plan`; never guess it, and never send image_gate_ack=true unless the user explicitly said the screens do not matter."""

# The description of `qa_stage_jira`, verbatim (continuation lines keep their indent).
STAGE_JIRA_DOC = """Stage ONE raw Jira fetch result right after you fetch it. Call it after each getJiraIssue/JQL call the qa_prepare_test_cases/qa_generate_test_cases directive asked for: part='issue', 'parent' or 'siblings'. `json` is that ONE result, stringified (json.dumps(result)), under ~20KB. When every part is staged, call qa_prepare_test_cases again with the SAME feature_or_url and `stage_token`, not jira_content_json."""

# The description of `qa_prepare_test_cases`, verbatim (continuation lines keep their indent).
PREPARE_DOC = """HOST-MODE generation: returns a grounded generation payload for YOU, the host model, to run; the server calls no LLM. Call it first for any test-case request.

        feature_or_url: a description, Jira/issue URL, web page URL or Swagger/OpenAPI URL. A Jira URL first returns a DIRECTIVE: fetch with your own `mcp__atlassian__getJiraIssue`, `qa_stage_jira` each raw result, then call again with the SAME feature_or_url and `stage_token` (or `jira_content_json` as a JSON STRING, json.dumps(result)).

        With `orchestration` in the payload: generate each category, stage it with `qa_submit_category`, then `qa_prep_status`, then `qa_submit_suite`. Otherwise generate the full suite and call `qa_submit_suite`.

        IMAGE GATE. ASK FIRST: for a Jira URL, ask the USER where the ticket's screens come from before your first call and pass `source_plan` (jira, jira_attach, jira_device, jira_both, device); never guess it, and never send image_gate_ack=true unless the user explicitly said the screens do not matter.

        Under-specified ticket: relay the questions, or pass proceed_anyway=true. Each prepared (non-clarify) reply carries the standing rules."""

# The description of `qa_search_corpus`, verbatim (continuation lines keep their indent).
SEARCH_CORPUS_DOC = """Search the RAG corpus for similar past test cases or bug reports. entry_type is 'test_case' or 'bug_report'; pass
        feature to narrow results to entries stored for that feature."""

# The description of `qa_configure_jira`, verbatim (continuation lines keep their indent).
CONFIGURE_JIRA_DOC = """DEPRECATED: Jira needs no credentials here; tickets are read through YOUR OWN Atlassian MCP connection. This tool returns connection steps and stores NOTHING; never ask for an API token. If a ticket URL fails, call qa_prepare_test_cases and follow its directive. To VERIFY the connection, call with no arguments (it directs you to mcp__atlassian__atlassianUserInfo), then again with atlassian_verify_json set to that RAW JSON result (or {"error": "..."})."""

# The description of `qa_capture_screens`, verbatim (continuation lines keep their indent).
CAPTURE_SCREENS_DOC = """Capture screenshots from a connected phone/emulator/simulator: images PLUS one capture_id per screen. Use it to ground test cases in REAL screens, especially for a Jira ticket (its images are unreadable here): pass the `capture_ids` to `qa_prepare_test_cases`/`qa_generate_test_cases`. `count` = screens. `peek`=true: text. `names` (comma-separated) ONLY if the user told you; never ask. `serial` aliases `device_id`; omit it for a picker; ids expire after 30 minutes."""
