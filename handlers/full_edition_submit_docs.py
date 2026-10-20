"""Tool descriptions of the submit MCP wrappers, attached as ``__doc__``."""

# The description of `qa_submit_bug_report`, verbatim.
SUBMIT_BUG_REPORT_DOC = """Submit the bug report YOU wrote for a task opened by qa_bug_report: the task_id and the full markdown as `report`, as the envelope's system_prompt specifies. Returns the finished report or asks you to re-emit it against a NEW task id."""

# The description of `qa_push_suite`, verbatim.
PUSH_SUITE_DOC = """Push a stored test suite into TestRail or Xray. target="testrail" (needs the numeric project_id from the TestRail URL) or "xray". Defaults to a PREVIEW that sends nothing. A real push needs apply=true AND the target's kill-switch flag enabled in .env. Nothing here can delete the cases afterwards."""

# The description of `qa_api_project`, verbatim.
API_PROJECT_DOC = """Create a new API test project, or continue an existing one. Every API flow starts here. With NO arguments it returns the choice plus registered projects: ask the tester in plain chat. create="<name>" fetches the public template, renames it, makes ONE local commit (nothing pushed) and proves it compiles. use="<name or path>" continues an existing project. Then call qa_prepare_api_tests."""

# The description of `qa_prepare_api_tests`, verbatim.
PREPARE_API_TESTS_DOC = """Start (or continue) an API endpoint test intake. Chat-only: no model call. Paste a contract template, curl command, OpenAPI URL/JSON or prose. Returns an intake card (questions to ask) or, once complete and confirmed=true, a task envelope YOU answer, then call qa_submit_api_tests with the task_id and your cases. project="<name>" scopes the endpoint registry; pass it once, on the first call."""

# The description of `qa_submit_api_tests`, verbatim.
SUBMIT_API_TESTS_DOC = """Submit the API test cases YOU generated for a qa_prepare_api_tests task: the task_id and your JSON {"cases": [...]}. The server grounds every assertion against the confirmed contract (dropping hallucinated fields, refusing cases that cannot fail) and returns the suite + a suite_id for qa_write_api_test."""

# The description of `qa_write_api_test`, verbatim.
WRITE_API_TEST_DOC = """Render + (dry-run or) write the Java tests for a finalized suite. apply=false (default) returns the branch, target paths and Java source, writing nothing. apply=true writes via the framework repo's ops pipeline, only when QA_API_FRAMEWORK_WRITE_ENABLED is on and QA_API_FRAMEWORK_WRITE_DRY_RUN is off. Never main, never push. project="<name>" targets a qa_api_project project; omit it for QA_API_FRAMEWORK_PATH."""

# The description of `qa_submit_explore_step`, verbatim.
SUBMIT_EXPLORE_STEP_DOC = """Submit the coaching step YOU wrote for a task opened by qa_explore_step. Include the trailing <meta>area: …; phase: …</meta> line the system_prompt asks for: the server parses it to track coverage, then strips it."""
