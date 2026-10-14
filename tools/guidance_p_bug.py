"""MCP Prompt bodies: bug, explore, API (leaf of tools/guidance.py)."""

from __future__ import annotations

_P_BUG = """\
Write a structured bug report from what the tester describes.

1. Call `qa_bug_report` with their description, however rough. It returns a
   task_id and a brief: the sections to write, the evidence to chase, and what
   this tracker expects.
2. Ask the tester for anything the brief flags as missing -- build, device,
   steps, actual versus expected. One round of questions, not an interrogation.
3. Write the report yourself against the brief. You are the model here.
4. Call `qa_submit_bug_report` with the task_id and your report text.

If the reply is a markdown menu, your client could not show a dialog: put the
menu to the tester and re-call with the identifier that menu names -- a
backticked `key`, a `device_id` or an adb `serial`, whichever it prints.
"""

_P_EXPLORE = """\
Coach a tester through an exploratory testing session, one step at a time.

1. Call `qa_explore_step` with the charter or feature under test. It returns a
   task_id and the brief for the NEXT step -- one step, not a plan.
2. Turn the brief into a concrete instruction the tester can act on now, and
   wait for what they observed.
3. Call `qa_submit_explore_step` with the task_id and their finding written up.
4. Repeat from step 1 for the next step. The session adapts to what they hit,
   so do not batch steps ahead or invent the next one yourself.

Stop when the tester says the charter is covered, and offer to turn what they
found into a bug report.
"""

_P_API = """\
Generate REST-Assured / TestNG API tests into a Java project.

1. Call `qa_api_project` to create a project from the configured template, or
   to continue with an existing one. The reply names the project marker it
   wrote or found -- that file, not a convention, decides the package.
2. Call `qa_prepare_api_tests` with the endpoints, spec URL, or description.
   It collects what it still needs first; while the intake status is
   `collecting` it is asking you for more, and it turns `confirmable` only when
   it has enough. Confirm with the TESTER at that point, not before.
3. Generate the cases and call `qa_submit_api_tests` with them.
4. Call `qa_write_api_test` to render Java into the project. Always call it
   WITHOUT `apply` first and show the tester the dry run. A real write needs
   `apply=true` AND a framework path AND this install configured for real
   writes -- installs ship dry-run-first, so `apply=true` alone still returns a
   dry run. The dry run itself names which of the three this machine is
   missing, so read its reply rather than guessing -- `qa-doctor` does NOT
   report the API framework path or the write flags (measured 2026-09-02; it
   used to be pointed at here, and did not carry the answer).

Never write into the tester's repo without showing them the dry run first.
"""
