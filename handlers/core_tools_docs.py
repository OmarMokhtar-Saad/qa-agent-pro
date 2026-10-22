"""Tool descriptions of the core and corpus MCP wrappers, attached as ``__doc__``."""

# The description of `qa_generate_test_cases`, verbatim (continuation lines keep their indent).
GENERATE_DOC = """Generate a suite: qa_prepare_test_cases flow, same args (Jira: fetch, THEN ASK the USER `source_plan`); omit feature_or_url if unsaid. Returns a PREPARE PAYLOAD YOU write cases for; relay the .xlsx path."""

# The description of `qa_stage_jira`, verbatim (continuation lines keep their indent).
STAGE_JIRA_DOC = """Stage a Jira result: part='issue'|'parent'|'siblings'; `json` = it stringified, or `path` = absolute file (big)."""

# The description of `qa_prepare_test_cases`, verbatim (continuation lines keep their indent).
PREPARE_DOC = """HOST-MODE generation: returns a payload YOU run (no server LLM); call first for any test-case request. Existing suites: ASK reuse/open/regenerate (regenerate = proceed_anyway=true). feature_or_url: text or Jira/web/Swagger URL (Jira: follow the DIRECTIVE, fetch FIRST, then ASK the USER the screen source (`source_plan`)). Rules arrive in the reply."""

# The description of `qa_search_corpus`, verbatim (continuation lines keep their indent).
SEARCH_CORPUS_DOC = """Search the RAG corpus for similar past test cases or bug reports. entry_type is 'test_case' or 'bug_report'; pass
        feature to narrow results to entries stored for that feature."""

# The description of `qa_configure_jira`, verbatim (continuation lines keep their indent).
CONFIGURE_JIRA_DOC = """DEPRECATED: Jira needs no credentials here; tickets are read through YOUR OWN Atlassian MCP connection. Stores NOTHING; never ask for an API token. If a ticket URL fails, call qa_prepare_test_cases and follow its directive."""

# The description of `qa_capture_screens`, verbatim (continuation lines keep their indent).
CAPTURE_SCREENS_DOC = """Capture screens from a phone/emulator: one capture_id per screen (kept server-side, never sent to you); ground cases in REAL screens (pass `capture_ids` to qa_prepare_test_cases). `count` = screens; `peek`=true: text; `names` ONLY if the user told you. `serial` aliases `device_id`."""
