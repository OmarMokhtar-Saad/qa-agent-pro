"""Server ``instructions`` text blocks (leaf of tools/guidance.py)."""

from __future__ import annotations

_INSTRUCTIONS_CORE = """\
QA Agents turns a feature or a ticket into a professional test suite, a bug
report, or a guided exploratory session, for testers who do not write code. NO
model runs on this server: YOU generate every artifact and hand it back through
the matching submit tool. New machine, or odd behaviour: `qa-doctor` first.
Before proposing ANY install or provision command: `qa_host_check` first.

TEST CASES -- in this order:
1. `qa_prepare_test_cases` returns a prep_id, the prompts, and the per-category
   job list. Nothing is generated yet.
2. `qa_get_category_job(prep_id, "all")` returns EVERY job packet in ONE call.
   Never one call per category, never re-fetch a packet you hold.
3. Generate each category and call `qa_submit_category` for each AS SOON AS
   it is written, so a reload cannot lose it, and so the server's duplicate
   prescreen sees the staged set. `qa_prep_status` shows what is outstanding.
4. `qa_submit_suite` with the prep_id finalizes -- or hands back gaps to fix
   and resubmit under the SAME prep_id.
5. The submit already wrote an .xlsx -- relay ITS path, and never ask which
   format they want. `qa_export_suite` only if the tester names another.
When they have not said where the feature comes from, call
`qa_generate_test_cases` with no arguments -- it asks them itself.

JIRA: this server never calls Jira. A ticket URL comes back as a DIRECTIVE
naming the tool YOUR client must call (`mcp__atlassian__getJiraIssue`, at the
prefix it prints); make that call, pass its RAW JSON back unmodified in the
argument named. Setup: `qa_configure_jira`. Prior work: `qa_search_corpus`.
Jira auth fails: STOP, user reconnects; never read credentials/keychains.

REPLIES THAT ARE NOT THE ANSWER, and the only correct response to each:
* a DIRECTIVE -- run the named call, return with its raw result;
* a markdown menu -- put it to the TESTER, then re-call with what it says to
  send back: a `key`, a `device_id`, a `serial`, or the number it asks for;
* a refusal naming an acknowledgement argument (`proceed_anyway`,
  `image_gate_ack`, ...) -- explain the concern, re-send with that argument
  only after they say go, never on the turn you were refused.
"""

_INSTRUCTIONS_FULL = """\
BUGS AND EXPLORATION are prepare/submit pairs too -- `qa_bug_report` then
`qa_submit_bug_report`, `qa_explore_step` then `qa_submit_explore_step`: YOU
write the text between them. `qa_wizard` helps a tester choose.
"""

_INSTRUCTIONS_API = """\
API TESTS: `qa_api_project`, then `qa_prepare_api_tests`,
`qa_submit_api_tests`, `qa_write_api_test`.
"""
# Three lines. The first is the no-raw-adb rule (see MAX_INSTRUCTION_LINES);
# the other two buy their place too: the two failure modes an ambient block
# can actually prevent here are re-fetching a packet the model already holds
# (it costs the tester's own tokens) and starting over in a new chat instead of
# resuming. Everything else -- credentials, the guard, the budgets -- is in the
# `qa_mobile_run` PROMPT, which a client pays for only when it is invoked.
_INSTRUCTIONS_MOBILE = """\
MOBILE: For anything on an Android emulator or device use the qa_mobile_test tool (goal=... for ad-hoc steps). Never run adb/uiautomator/ADB Keyboard directly; the tool owns the safety guard, keyboard handling and evidence. Never report a screen state, field value or login outcome that was not read from a qa_* observation. If the server cannot type or act, stop and report the blocker by name. Do not fall back to raw adb input, and do not claim a result.
`qa_mobile_test` gives ONE packet at a time -- answer it with
`qa_submit_mobile_step`, never re-fetch it; resume in any chat by run id. `qa_setup_capture` installs the API-capture certificate ahead of a run.
"""
